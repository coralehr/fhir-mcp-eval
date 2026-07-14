#!/usr/bin/env python3
"""Emit an aggregate-only receipt for an A11 candidate FHIR bulk archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCANNER_VERSION = "a11-substrate-audit-v1"


@dataclass(frozen=True)
class ChainFamily:
    name: str
    root_type: str
    edges: tuple[tuple[str, str], ...]


GENERIC_FAMILIES = (
    ChainFamily(
        "DiagnosticReport.result -> Observation.encounter -> Encounter.episodeOfCare",
        "DiagnosticReport",
        (
            ("result", "Observation"),
            ("encounter", "Encounter"),
            ("episodeOfCare", "EpisodeOfCare"),
        ),
    ),
    ChainFamily(
        "Procedure.reasonReference -> Condition.encounter -> Encounter.episodeOfCare",
        "Procedure",
        (
            ("reasonReference", "Condition"),
            ("encounter", "Encounter"),
            ("episodeOfCare", "EpisodeOfCare"),
        ),
    ),
    ChainFamily(
        "DocumentReference.context.encounter -> Encounter.episodeOfCare",
        "DocumentReference",
        (("context.encounter", "Encounter"), ("episodeOfCare", "EpisodeOfCare")),
    ),
    ChainFamily(
        "ServiceRequest.encounter -> Encounter.episodeOfCare",
        "ServiceRequest",
        (("encounter", "Encounter"), ("episodeOfCare", "EpisodeOfCare")),
    ),
    ChainFamily(
        "Observation.encounter -> Encounter.episodeOfCare",
        "Observation",
        (("encounter", "Encounter"), ("episodeOfCare", "EpisodeOfCare")),
    ),
    ChainFamily(
        "MedicationRequest.reasonReference -> Condition.encounter",
        "MedicationRequest",
        (("reasonReference", "Condition"), ("encounter", "Encounter")),
    ),
)

A11_FAMILIES = (
    ChainFamily(
        "Observation.hasMember -> Observation.hasMember",
        "Observation",
        (("hasMember", "Observation"), ("hasMember", "Observation")),
    ),
    ChainFamily(
        "Observation.hasMember -> Observation.specimen",
        "Observation",
        (("hasMember", "Observation"), ("specimen", "Specimen")),
    ),
    ChainFamily(
        "DiagnosticReport.result -> Observation.hasMember",
        "DiagnosticReport",
        (("result", "Observation"), ("hasMember", "Observation")),
    ),
    ChainFamily(
        "DiagnosticReport.result -> Observation.specimen",
        "DiagnosticReport",
        (("result", "Observation"), ("specimen", "Specimen")),
    ),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_resources(path: Path) -> list[dict[str, Any]]:
    resources: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if not name.endswith(".ndjson") or name.endswith("/log.ndjson"):
                continue
            for line_number, raw_line in enumerate(
                archive.read(name).splitlines(), start=1
            ):
                if not raw_line.strip():
                    continue
                value = json.loads(raw_line)
                if not isinstance(value, dict):
                    raise ValueError(f"{name}:{line_number} is not a FHIR object")
                if not isinstance(value.get("resourceType"), str) or not isinstance(
                    value.get("id"), str
                ):
                    raise ValueError(f"{name}:{line_number} has no resource identity")
                resources.append(value)
    if not resources:
        raise ValueError("candidate archive has no FHIR resources")
    return resources


def references_at(resource: dict[str, Any], path: str) -> list[str]:
    values: list[Any] = [resource]
    for field in path.split("."):
        children: list[Any] = []
        for value in values:
            if not isinstance(value, dict) or field not in value:
                continue
            child = value[field]
            children.extend(child if isinstance(child, list) else [child])
        values = children
    return [
        value["reference"]
        for value in values
        if isinstance(value, dict) and isinstance(value.get("reference"), str)
    ]


def patient_owner(resource: dict[str, Any]) -> str | None:
    for field in ("subject", "patient", "beneficiary"):
        references = references_at(resource, field)
        if references and references[0].startswith("Patient/"):
            return references[0]
    if resource.get("resourceType") == "Patient":
        return f"Patient/{resource['id']}"
    return None


def audit_family(
    family: ChainFamily,
    resources: list[dict[str, Any]],
    index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    root_refs: set[str] = set()
    patient_refs: set[str] = set()
    path_count = 0
    rejected_ambiguous_roots = 0
    rejected_ambiguous_target_edges = 0
    rejected_cross_patient_edges = 0
    for root in resources:
        if root.get("resourceType") != family.root_type:
            continue
        root_owner = patient_owner(root)
        if root_owner is None:
            rejected_ambiguous_roots += 1
            continue
        frontier = [root]
        for field, target_type in family.edges:
            next_frontier: list[dict[str, Any]] = []
            for source in frontier:
                for reference in references_at(source, field):
                    target = index.get(reference)
                    if target is not None and target.get("resourceType") == target_type:
                        target_owner = patient_owner(target)
                        if target_owner is None:
                            rejected_ambiguous_target_edges += 1
                        elif target_owner != root_owner:
                            rejected_cross_patient_edges += 1
                        else:
                            next_frontier.append(target)
            frontier = next_frontier
            if not frontier:
                break
        if not frontier:
            continue
        root_ref = f"{family.root_type}/{root['id']}"
        root_refs.add(root_ref)
        path_count += len(frontier)
        patient_refs.add(root_owner)
    return {
        "family": family.name,
        "roots": len(root_refs),
        "paths": path_count,
        "patient_clusters": len(patient_refs),
        "rejected_ambiguous_roots": rejected_ambiguous_roots,
        "rejected_ambiguous_target_edges": rejected_ambiguous_target_edges,
        "rejected_cross_patient_edges": rejected_cross_patient_edges,
    }


def build_receipt(
    archive_path: Path, *, source_url: str, source_commit: str
) -> dict[str, Any]:
    resources = load_resources(archive_path)
    index: dict[str, dict[str, Any]] = {}
    for resource in resources:
        reference = f"{resource['resourceType']}/{resource['id']}"
        if reference in index:
            raise ValueError(f"duplicate FHIR resource identity: {reference}")
        index[reference] = resource
    counts = Counter(str(resource["resourceType"]) for resource in resources)
    return {
        "schema_version": SCANNER_VERSION,
        "source": {
            "archive": archive_path.name,
            "archive_sha256": sha256_file(archive_path),
            "repository": source_url,
            "commit": source_commit,
        },
        "scanner": {
            "path": Path(__file__).name,
            "sha256": sha256_file(Path(__file__).resolve()),
        },
        "aggregate_only": True,
        "total_resources": len(resources),
        "resource_counts": dict(sorted(counts.items())),
        "generic_path_families": [
            audit_family(family, resources, index) for family in GENERIC_FAMILIES
        ],
        "registered_a11_path_families": [
            audit_family(family, resources, index) for family in A11_FAMILIES
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    receipt = build_receipt(
        args.archive,
        source_url=args.source_url,
        source_commit=args.source_commit,
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
