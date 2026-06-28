#!/usr/bin/env python3
"""Build A7 Bonfire-complete frozen packets.

A7 starts from the A6 query-aware selection layer, then adds the product-shaped
pieces the roadmap cares about: reference resolution, code summaries, source
citations, a read-contract envelope, and explicit insufficiency metadata.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import a6_packet_builder as a6


REFERENCE_PREFIXES_TO_SKIP = {"Patient"}


def _resource_id(resource: dict[str, Any]) -> str | None:
    return a6._resource_id(resource)


def _json(value: Any) -> str:
    return a6._json(value)


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def extract_references(resources: list[dict[str, Any]]) -> list[str]:
    refs = set()
    for resource in resources:
        for item in _walk(resource):
            ref = item.get("reference") if isinstance(item, dict) else None
            if not isinstance(ref, str) or "/" not in ref:
                continue
            prefix, ident = ref.split("/", 1)
            if prefix in REFERENCE_PREFIXES_TO_SKIP or not ident:
                continue
            refs.add(ref)
    return sorted(refs)


def extract_codes(resources: list[dict[str, Any]]) -> list[dict[str, str]]:
    codes = {}
    for resource in resources:
        source = _resource_id(resource)
        for item in _walk(resource):
            coding = item.get("coding") if isinstance(item, dict) else None
            if not isinstance(coding, list):
                continue
            for code in coding:
                if not isinstance(code, dict):
                    continue
                entry = {
                    "system": str(code.get("system") or ""),
                    "code": str(code.get("code") or ""),
                    "display": str(code.get("display") or ""),
                    "source_resource_id": source or "",
                }
                key = (entry["system"], entry["code"], entry["display"], entry["source_resource_id"])
                if any(entry.values()):
                    codes[key] = entry
    return [codes[k] for k in sorted(codes)]


def _first_present(resource: dict[str, Any], names: list[str]) -> Any:
    for name in names:
        value = resource.get(name)
        if value:
            return value
    period = resource.get("period")
    if isinstance(period, dict):
        return period.get("start") or period.get("end")
    return None


def _code_text(resource: dict[str, Any]) -> str | None:
    code = resource.get("code")
    if not isinstance(code, dict):
        return None
    if code.get("text"):
        return str(code["text"])
    for coding in code.get("coding") or []:
        if isinstance(coding, dict) and coding.get("display"):
            return str(coding["display"])
    return None


def build_citations(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations = []
    for resource in resources:
        rid = _resource_id(resource)
        if not rid:
            continue
        meta = resource.get("meta") if isinstance(resource.get("meta"), dict) else {}
        citations.append(
            {
                "resource_id": rid,
                "resource_type": resource.get("resourceType"),
                "version_id": meta.get("versionId"),
                "date": _first_present(
                    resource,
                    [
                        "effectiveDateTime",
                        "authoredOn",
                        "date",
                        "performedDateTime",
                        "onsetDateTime",
                        "recordedDate",
                    ],
                ),
                "code_text": _code_text(resource),
            }
        )
    return sorted(citations, key=lambda item: item["resource_id"])


def fetch_referenced_resources(refs: list[str]) -> dict[str, dict[str, Any]]:
    from fhir_client import get_fhir_client

    grouped = defaultdict(list)
    for ref in refs:
        resource_type, ident = ref.split("/", 1)
        grouped[resource_type].append(ident)

    client = get_fhir_client()
    resolved = {}
    for resource_type, ids in grouped.items():
        try:
            for resource in client.get_resources_by_resource_ids(resource_type, ids):
                rid = _resource_id(resource)
                if rid:
                    resolved[rid] = resource
        except Exception as exc:
            resolved[f"OperationOutcome/{resource_type}"] = {
                "resourceType": "OperationOutcome",
                "id": resource_type,
                "issue": [{"diagnostics": str(exc)}],
            }
    return resolved


def _read_contract(row: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    return {
        "patient_fhir_id": row.get("patient_fhir_id"),
        "resource_types": intent["resource_types"],
        "date_windows": intent["date_windows"],
        "temporal_policy": intent["temporal_policy"],
        "required_citations": True,
        "writes_allowed": False,
        "selection_policy": "query-aware primary fetch + deterministic reference resolution",
    }


def _insufficiency(plan_only: bool, resources: list[dict[str, Any]]) -> dict[str, Any] | None:
    if plan_only:
        return {"reason": "plan_only_no_live_fetch"}
    if not resources:
        return {"reason": "no_resources_returned"}
    if all(resource.get("resourceType") == "OperationOutcome" for resource in resources):
        return {"reason": "only_operation_outcomes_returned"}
    return None


def _dedupe_resources(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return a6._dedupe_resources(resources)


def build_packet_record(
    row: dict[str, Any],
    *,
    plan_only: bool,
    resources_by_query: dict[str, list[dict[str, Any]]] | None,
    referenced_resources_by_id: dict[str, dict[str, Any]] | None = None,
    count: int = 100,
) -> dict[str, Any]:
    safe = {k: v for k, v in row.items() if k not in a6.GOLD_FIELDS}
    intent = a6.infer_intent(safe)
    primary_plan = a6.build_search_plan(safe, intent, count=count)
    resources_by_query = resources_by_query or {}

    primary_resources = []
    for item in primary_plan:
        primary_resources.extend(resources_by_query.get(item["path"], []))
    primary_resources = _dedupe_resources(primary_resources)

    needed_refs = extract_references(primary_resources)
    referenced_resources_by_id = referenced_resources_by_id or {}
    referenced_resources = [referenced_resources_by_id[ref] for ref in needed_refs if ref in referenced_resources_by_id]
    all_resources = _dedupe_resources(primary_resources + referenced_resources)
    resource_ids = sorted(rid for rid in (_resource_id(resource) for resource in all_resources) if rid)

    packet = {
        "kind": "a7_bonfire_complete_packet",
        "plan_only": plan_only,
        "read_contract": _read_contract(safe, intent),
        "resources": [] if plan_only else all_resources,
        "resource_count": 0 if plan_only else len(all_resources),
        "source_resource_ids": [] if plan_only else resource_ids,
        "source_queries": primary_plan,
        "reference_resolution": {
            "requested": needed_refs,
            "resolved": sorted(ref for ref in needed_refs if ref in referenced_resources_by_id),
            "unresolved": sorted(ref for ref in needed_refs if ref not in referenced_resources_by_id),
        },
        "terminology": [] if plan_only else extract_codes(all_resources),
        "citations": [] if plan_only else build_citations(all_resources),
        "insufficiency": _insufficiency(plan_only, all_resources),
    }
    packet["sha256"] = a6.sha256_text(_json(packet))
    return {
        "question_id": safe.get("question_id"),
        "question": safe.get("question"),
        "patient_fhir_id": safe.get("patient_fhir_id"),
        "assumption": safe.get("assumption"),
        "intent": intent,
        "packet": packet,
    }


def fetch_complete_resources(primary_plan: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    resources_by_query = a6.fetch_resources(primary_plan)
    primary = []
    for resources in resources_by_query.values():
        primary.extend(resources)
    refs = extract_references(_dedupe_resources(primary))
    return resources_by_query, fetch_referenced_resources(refs)


def write_manifest(path: Path, *, input_path: Path, output_path: Path, args: argparse.Namespace, records: list[dict[str, Any]]) -> None:
    manifest = {
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "kind": "a7_bonfire_complete_packet_manifest",
        "input": {"path": str(input_path), "sha256": a6.sha256_file(input_path)},
        "output": {"path": str(output_path), "sha256": a6.sha256_file(output_path)},
        "config": {
            "limit": args.limit,
            "count": args.count,
            "plan_only": args.plan_only,
            "split": args.split,
        },
        "questions": len(records),
        "packet_hashes": {str(r["question_id"]): r["packet"]["sha256"] for r in records},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build A7 Bonfire-complete frozen packets.")
    parser.add_argument("--input", type=Path, default=Path("final_dataset/full_test409.csv"))
    parser.add_argument("--output", type=Path, default=Path("runs/a7_bonfire_packets.jsonl"))
    parser.add_argument("--manifest", type=Path, default=Path("runs/a7_bonfire_manifest.json"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--split", default="test")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    rows = a6.load_rows(args.input, limit=args.limit, split=args.split)
    records = []
    for row in rows:
        intent = a6.infer_intent(row)
        plan = a6.build_search_plan(row, intent, count=args.count)
        if args.plan_only:
            resources_by_query, referenced = {}, {}
        else:
            resources_by_query, referenced = fetch_complete_resources(plan)
        records.append(
            build_packet_record(
                row,
                plan_only=args.plan_only,
                resources_by_query=resources_by_query,
                referenced_resources_by_id=referenced,
                count=args.count,
            )
        )

    a6.write_jsonl(args.output, records)
    write_manifest(args.manifest, input_path=args.input, output_path=args.output, args=args, records=records)
    print(json.dumps({"output": str(args.output), "manifest": str(args.manifest), "records": len(records), "plan_only": args.plan_only}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
