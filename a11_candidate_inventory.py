#!/usr/bin/env python3
"""Build an aggregate-only A11 topology inventory from sealed QT-4 packets."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from pathlib import Path
from typing import Any


def _resource_ref(resource: dict[str, Any]) -> str | None:
    resource_type = resource.get("resourceType")
    resource_id = resource.get("id")
    return f"{resource_type}/{resource_id}" if resource_type and resource_id else None


def _clinical_time(resource: dict[str, Any]) -> str | None:
    resource_type = resource.get("resourceType")
    period = resource.get("effectivePeriod")
    period = period if isinstance(period, dict) else {}
    if resource_type in {"Observation", "DiagnosticReport"}:
        for value in (
            resource.get("effectiveDateTime"),
            period.get("end"),
            period.get("start"),
        ):
            if isinstance(value, str) and value:
                return value
    return None


def _edge_relation(receipt: dict[str, Any]) -> str:
    source_type = str(receipt["from"]).split("/", 1)[0]
    path = str(receipt.get("path") or "")
    if path.startswith("/"):
        field = next((segment for segment in path.split("/") if segment), "reference")
    else:
        segments = path.split(".")
        field = segments[1] if len(segments) > 1 else segments[0]
        field = field.split("[", 1)[0]
    return f"{source_type}.{field}"


def _canonical_reference(reference: str) -> tuple[str, str | None] | None:
    parts = reference.split("/")
    if len(parts) == 2 and all(parts):
        return reference, None
    if len(parts) == 4 and parts[2] == "_history" and all(parts):
        return f"{parts[0]}/{parts[1]}", parts[3]
    return None


def _reference_at_path(resource: dict[str, Any], path: str) -> str | None:
    value: Any = resource
    if path.startswith("/"):
        segments = [
            segment.replace("~1", "/").replace("~0", "~")
            for segment in path.split("/")
            if segment
        ]
    else:
        segments = path.split(".")
        if segments and segments[0] == resource.get("resourceType"):
            segments = segments[1:]
    try:
        for segment in segments:
            if segment == "reference" and isinstance(value, dict):
                value = value.get("reference")
                continue
            match = re.fullmatch(r"([^\[]+)(?:\[(\d+)\])?", segment)
            if match is None or not isinstance(value, dict):
                return None
            value = value.get(match.group(1))
            if match.group(2) is not None:
                if not isinstance(value, list):
                    return None
                value = value[int(match.group(2))]
        if isinstance(value, dict):
            value = value.get("reference")
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _validate_receipt(
    receipt: dict[str, Any], resources: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any] | None, str | None]:
    source_parsed = _canonical_reference(str(receipt.get("from") or ""))
    target_value = str(receipt.get("to") or "")
    target_parsed = _canonical_reference(target_value)
    if source_parsed is None or target_parsed is None:
        return None, "malformed_reference"
    source_ref, source_version = source_parsed
    source = resources.get(source_ref)
    if source is None:
        return None, "missing_source"
    current_source_version = str(source.get("meta", {}).get("versionId", "")) or None
    if source_version is not None and source_version != current_source_version:
        return None, "stale_source_version"
    path = str(receipt.get("path") or "")
    if _reference_at_path(source, path) != target_value:
        return None, "path_replay_mismatch"
    status = str(receipt.get("status") or "unknown")
    if status not in {"fetched", "already_present", "missing", "max_resources"}:
        return None, "unsupported_status"
    if status in {"fetched", "already_present"}:
        target_ref, target_version = target_parsed
        target = resources.get(target_ref)
        if target is None:
            return None, "missing_fetched_target"
        current_target_version = str(target.get("meta", {}).get("versionId", "")) or None
        if target_version is not None and target_version != current_target_version:
            return None, "stale_target_version"
    normalized = dict(receipt)
    normalized["canonical_from"] = source_ref
    normalized["canonical_to"] = target_parsed[0]
    return normalized, None


def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get("packet"), dict):
            raise ValueError(f"invalid packet row at line {line_number}")
        rows.append(row)
    return rows


def scan_packet_file(path: Path) -> dict[str, Any]:
    rows = _read_rows(path)
    dispatched = [
        row
        for row in rows
        if "micro-vocab" in row["packet"].get("features", [])
    ]
    status_counts: collections.Counter[str] = collections.Counter()
    edge_counts: collections.Counter[str] = collections.Counter()
    two_hop_counts: collections.Counter[str] = collections.Counter()
    rows_with_fetched: set[int] = set()
    rows_with_depth_two_fetched: set[int] = set()
    rows_with_multiple_timed_roots: set[int] = set()
    patients: set[str] = set()
    resource_refs: set[str] = set()
    rejected_receipts: collections.Counter[str] = collections.Counter()

    for row_index, row in enumerate(dispatched):
        packet = row["packet"]
        patient = row.get("patient_fhir_id")
        if patient is not None:
            patients.add(str(patient))
        resources = {
            reference: resource
            for resource in packet.get("resources", [])
            if isinstance(resource, dict)
            and (reference := _resource_ref(resource)) is not None
        }
        resource_refs.update(resources)
        traversal = packet.get("reference_traversal")
        traversal = traversal if isinstance(traversal, dict) else {}
        receipts = traversal.get("path_receipts")
        receipts = receipts if isinstance(receipts, list) else []
        by_source: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
        valid_receipts: list[dict[str, Any]] = []
        for receipt in receipts:
            if not isinstance(receipt, dict):
                raise ValueError("path receipt must be an object")
            validated, rejection = _validate_receipt(receipt, resources)
            if rejection is not None:
                rejected_receipts[rejection] += 1
                continue
            assert validated is not None
            valid_receipts.append(validated)
        for receipt in valid_receipts:
            status = str(receipt.get("status") or "unknown")
            status_counts[status] += 1
            edge_counts[_edge_relation(receipt)] += 1
            by_source[receipt["canonical_from"]].append(receipt)
            if status in {"fetched", "already_present"}:
                rows_with_fetched.add(row_index)

        root_refs = {
            receipt["canonical_from"]
            for receipt in valid_receipts
            if int(receipt.get("depth") or 0) == 1
        }
        timed_roots = [
            reference
            for reference in root_refs
            if reference in resources and _clinical_time(resources[reference])
        ]
        if len(timed_roots) >= 2:
            rows_with_multiple_timed_roots.add(row_index)

        for first in valid_receipts:
            if int(first.get("depth") or 0) != 1 or first.get("status") not in {"fetched", "already_present"}:
                continue
            for second in by_source.get(first["canonical_to"], []):
                if int(second.get("depth") or 0) != 2 or second.get("status") not in {"fetched", "already_present"}:
                    continue
                rows_with_depth_two_fetched.add(row_index)
                family = f"{_edge_relation(first)} -> {_edge_relation(second)}"
                two_hop_counts[family] += 1

    return {
        "schema_version": "a11-candidate-inventory-v1",
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "sealed_packet_rows": len(rows),
        "dispatched_micro_rows": len(dispatched),
        "unique_dispatched_patients": len(patients),
        "unique_resources_visible_across_dispatched_packets": len(resource_refs),
        "path_status_counts": dict(sorted(status_counts.items())),
        "edge_counts": dict(sorted(edge_counts.items())),
        "available_two_hop_paths_by_family": dict(sorted(two_hop_counts.items())),
        "rows_with_any_fetched_target": len(rows_with_fetched),
        "rows_with_depth_two_fetched_target": len(rows_with_depth_two_fetched),
        "rows_with_multiple_timed_roots": len(rows_with_multiple_timed_roots),
        "rejected_receipts_by_reason": dict(sorted(rejected_receipts.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = scan_packet_file(args.packets)
    rendered = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
