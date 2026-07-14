#!/usr/bin/env python3
"""Deterministic, zero-model mechanism gate for A11 path-required retrieval.

This module deliberately models a bounded projection over explicit FHIR
``Reference.reference`` values. It is not a graph database and it does not
make an answer-quality claim. The generated packets are pre-answer fixtures
used to prove that the registered arms differ only in their retrieval
mechanism before any model quota is spent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any, Iterable


ARM_STAR = "patient_star"
ARM_TRAVERSAL = "bounded_explicit_traversal"
ARM_VOCAB_TRAVERSAL = "vocabulary_bounded_traversal"
ARMS = (ARM_STAR, ARM_TRAVERSAL, ARM_VOCAB_TRAVERSAL)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_line(value: Any) -> bytes:
    return canonical_bytes(value) + b"\n"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resource_ref(resource: dict[str, Any]) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


def parse_relative_reference(reference: str) -> tuple[str, str, str | None] | None:
    """Parse a relative R4 reference, preserving an optional exact version.

    Absolute, fragment, URN, and malformed references are intentionally
    excluded from this benchmark. The production extractor has its own wider
    conformance surface; A11 tests only registered explicit relative paths.
    """

    if not reference or reference.startswith(("#", "urn:", "http://", "https://")):
        return None
    parts = reference.split("/")
    if len(parts) == 2 and all(parts):
        return parts[0], parts[1], None
    if len(parts) == 4 and parts[2] == "_history" and all(parts):
        return parts[0], parts[1], parts[3]
    return None


def escape_json_pointer(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def iter_explicit_references(value: Any, pointer: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        reference = value.get("reference")
        if isinstance(reference, str) and parse_relative_reference(reference):
            yield pointer or "/", reference
        for key in sorted(value):
            child_pointer = f"{pointer}/{escape_json_pointer(key)}"
            yield from iter_explicit_references(value[key], child_pointer)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_explicit_references(item, f"{pointer}/{index}")


def load_fixture(path: Path) -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if fixture.get("schema_version") != "a11-path-required-v1":
        raise ValueError("unsupported A11 fixture schema")
    case_ids: set[str] = set()
    for case in fixture.get("cases", []):
        case_id = case.get("case_id")
        if not case_id or case_id in case_ids:
            raise ValueError(f"duplicate or missing case id: {case_id}")
        case_ids.add(case_id)
        keys: set[tuple[str, str]] = set()
        for entry in case.get("resources", []):
            resource = entry.get("resource", {})
            key = (entry.get("practice_id"), resource_ref(resource))
            if key in keys:
                raise ValueError(f"duplicate resource key in {case_id}: {key}")
            keys.add(key)
        if case.get("minimum_evidence_hops", 0) < 2 and case.get("answerable"):
            raise ValueError(f"answerable case is not path-required: {case_id}")
        allowed_purposes = case.get("allowed_purposes")
        if not isinstance(allowed_purposes, list) or not allowed_purposes:
            raise ValueError(f"case has no allowed purposes: {case_id}")
        if not isinstance(case.get("max_targets"), int) or case["max_targets"] < 1:
            raise ValueError(f"case has invalid max targets: {case_id}")
        if (
            not isinstance(case.get("max_packet_bytes"), int)
            or case["max_packet_bytes"] < 1
        ):
            raise ValueError(f"case has invalid packet byte bound: {case_id}")
    if not case_ids:
        raise ValueError("fixture has no cases")
    return fixture


def _visible_index(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if case["principal"]["purpose"] not in case["allowed_purposes"]:
        return {}
    practice_id = case["principal"]["practice_id"]
    return {
        resource_ref(entry["resource"]): entry["resource"]
        for entry in case["resources"]
        if entry["practice_id"] == practice_id
    }


def _resolve(
    index: dict[str, dict[str, Any]], reference: str
) -> tuple[str, dict[str, Any]] | None:
    parsed = parse_relative_reference(reference)
    if not parsed:
        return None
    resource_type, resource_id, requested_version = parsed
    key = f"{resource_type}/{resource_id}"
    resource = index.get(key)
    if resource is None:
        return None
    current_version = str(resource.get("meta", {}).get("versionId", "")) or None
    if requested_version is not None and requested_version != current_version:
        return None
    return key, resource


def _directly_references(resource: dict[str, Any], target: str) -> bool:
    return any(reference == target for _, reference in iter_explicit_references(resource))


def _star_packet(case: dict[str, Any], index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    patient_ref = case["patient_ref"]
    included = {
        key: resource
        for key, resource in index.items()
        if key == patient_ref
        or key in case["seed_refs"]
        or _directly_references(resource, patient_ref)
    }
    return {
        "arm": ARM_STAR,
        "case_id": case["case_id"],
        "resources": [included[key] for key in sorted(included)],
        "path_citations": [],
        "bounds": {
            "max_depth": 0,
            "allowed_resource_types": [],
            "max_targets": case["max_targets"],
            "max_packet_bytes": case["max_packet_bytes"],
            "outcomes": (
                []
                if case["principal"]["purpose"] in case["allowed_purposes"]
                else ["purpose_denial"]
            ),
        },
    }


def _model_visible_packet_bytes(
    resources: dict[str, dict[str, Any]], citations: list[dict[str, Any]]
) -> int:
    return len(
        canonical_bytes(
            {
                "resources": [resources[key] for key in sorted(resources)],
                "path_citations": citations,
            }
        )
    )


def _traversal_packet(
    case: dict[str, Any],
    index: dict[str, dict[str, Any]],
    arm: str,
) -> dict[str, Any]:
    allowed_key = (
        "broad_allowed_resource_types"
        if arm == ARM_TRAVERSAL
        else "vocabulary_allowed_resource_types"
    )
    allowed_types = set(case[allowed_key])
    max_depth = int(case["max_depth"])
    max_targets = int(case["max_targets"])
    max_packet_bytes = int(case["max_packet_bytes"])
    included: dict[str, dict[str, Any]] = {}
    patient = index.get(case["patient_ref"])
    if patient:
        included[case["patient_ref"]] = patient

    queue: deque[tuple[str, int, list[dict[str, str]]]] = deque()
    seen_paths: set[tuple[str, tuple[tuple[str, str, str], ...]]] = set()
    for seed_ref in sorted(case["seed_refs"]):
        resource = index.get(seed_ref)
        if resource:
            included[seed_ref] = resource
            queue.append((seed_ref, 0, []))

    citations: list[dict[str, Any]] = []
    outcomes: set[str] = set()
    if case["principal"]["purpose"] not in case["allowed_purposes"]:
        outcomes.add("purpose_denial")
    included_targets = 0
    unavailable_paths: set[tuple[tuple[str, str, str], ...]] = set()
    while queue:
        source_ref, depth, path = queue.popleft()
        if depth >= max_depth:
            continue
        source = index[source_ref]
        for pointer, requested_target in iter_explicit_references(source):
            parsed = parse_relative_reference(requested_target)
            if not parsed or parsed[0] not in allowed_types:
                continue
            step = {
                "source": source_ref,
                "json_pointer": pointer,
                "target": requested_target,
            }
            next_path = [*path, step]
            path_key = tuple((part["source"], part["json_pointer"], part["target"]) for part in next_path)
            resolved = _resolve(index, requested_target)
            if resolved is None:
                if path_key not in unavailable_paths:
                    unavailable_paths.add(path_key)
                    citations.append(
                        {
                            "state": "unavailable",
                            "target": requested_target,
                            "steps": next_path,
                        }
                    )
                continue

            target_ref, target = resolved
            citation_key = (target_ref, path_key)
            if citation_key in seen_paths:
                continue
            if included_targets >= max_targets:
                outcomes.add("target_limit")
                continue
            seen_paths.add(citation_key)
            available_citation = {
                "state": "available",
                "target": target_ref,
                "steps": next_path,
            }
            candidate_included = {**included, target_ref: target}
            candidate_citations = [*citations, available_citation]
            if (
                _model_visible_packet_bytes(
                    candidate_included, candidate_citations
                )
                > max_packet_bytes
            ):
                outcomes.add("packet_byte_limit")
                continue
            included[target_ref] = target
            citations.append(available_citation)
            included_targets += 1
            if len(next_path) < max_depth:
                queue.append((target_ref, len(next_path), next_path))

    citations.sort(key=lambda item: canonical_bytes(item))
    packet = {
        "arm": arm,
        "case_id": case["case_id"],
        "resources": [included[key] for key in sorted(included)],
        "path_citations": citations,
        "bounds": {
            "max_depth": max_depth,
            "allowed_resource_types": sorted(allowed_types),
            "max_targets": max_targets,
            "max_packet_bytes": max_packet_bytes,
            "outcomes": sorted(outcomes),
        },
    }
    observed_bytes = _model_visible_packet_bytes(included, citations)
    packet["bounds"]["observed_packet_bytes"] = observed_bytes
    if observed_bytes > max_packet_bytes:
        raise ValueError(f"baseline packet exceeds max_packet_bytes: {case['case_id']}")
    return packet


def compile_case(case: dict[str, Any], arm: str) -> dict[str, Any]:
    if arm not in ARMS:
        raise ValueError(f"unknown arm: {arm}")
    index = _visible_index(case)
    packet = (
        _star_packet(case, index)
        if arm == ARM_STAR
        else _traversal_packet(case, index, arm)
    )
    packet_refs = {resource_ref(resource) for resource in packet["resources"]}
    expected = set(case["expected_evidence_refs"])
    forbidden = set(case.get("forbidden_resource_refs", []))
    leakage_count = len(packet_refs & forbidden)
    purpose_scope_allowed = (
        case["principal"]["purpose"] in case["allowed_purposes"]
    )

    if case["answerable"]:
        available_evidence = expected & packet_refs
        evidence_recall: float | None = len(available_evidence) / len(expected)
        cited_at_depth = {
            citation["target"]
            for citation in packet["path_citations"]
            if citation["state"] == "available"
            and len(citation["steps"]) >= case["minimum_evidence_hops"]
        }
        mechanism_success = expected <= packet_refs and expected <= cited_at_depth
    else:
        evidence_recall = None
        unavailable_targets = {
            citation["target"]
            for citation in packet["path_citations"]
            if citation["state"] == "unavailable"
        }
        expected_unavailable = set(case["expected_unavailable_refs"])
        expected_bound_outcomes = set(case.get("expected_bound_outcomes", []))
        mechanism_success = (
            leakage_count == 0
            and not (forbidden & packet_refs)
            and (arm == ARM_STAR or expected_unavailable <= unavailable_targets)
            and (
                arm == ARM_STAR
                or expected_bound_outcomes <= set(packet["bounds"]["outcomes"])
            )
        )

    return {
        "case_id": case["case_id"],
        "arm": arm,
        "answerable": case["answerable"],
        "failure_mode": case.get("failure_mode"),
        "mechanism_success": mechanism_success,
        "evidence_recall": evidence_recall,
        "authorization_leakage_count": leakage_count,
        "purpose_scope_allowed": purpose_scope_allowed,
        # Economics count only the evidence payload delivered to the answer
        # model. Arm labels and internal walker bounds are receipt metadata,
        # not model context, and would otherwise bias the arm comparison.
        "packet_bytes": len(
            canonical_bytes(
                {
                    "resources": packet["resources"],
                    "path_citations": packet["path_citations"],
                }
            )
        ),
        "packet": packet,
    }


def run_benchmark(fixture: dict[str, Any]) -> dict[str, Any]:
    results = [
        compile_case(case, arm)
        for arm in ARMS
        for case in sorted(fixture["cases"], key=lambda item: item["case_id"])
    ]
    aggregates: dict[str, Any] = {}
    for arm in ARMS:
        arm_results = [result for result in results if result["arm"] == arm]
        answerable = [result for result in arm_results if result["answerable"]]
        aggregate_recall = sum(result["evidence_recall"] or 0 for result in answerable) / len(answerable)
        aggregates[arm] = {
            "cases": len(arm_results),
            "mechanism_successes": sum(result["mechanism_success"] for result in arm_results),
            "answerable_evidence_recall": aggregate_recall,
            "authorization_leakage_count": sum(
                result["authorization_leakage_count"] for result in arm_results
            ),
            "packet_bytes": sum(result["packet_bytes"] for result in arm_results),
        }
    return {
        "benchmark_id": fixture["benchmark_id"],
        "schema_version": fixture["schema_version"],
        "results": results,
        "aggregates": aggregates,
    }


def write_artifacts(fixture: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    benchmark = run_benchmark(fixture)
    artifact_bytes: dict[str, bytes] = {}
    for arm in ARMS:
        rows = [result for result in benchmark["results"] if result["arm"] == arm]
        artifact_bytes[f"{arm}.jsonl"] = b"".join(canonical_line(row) for row in rows)
    artifact_bytes["mechanism-results.json"] = canonical_line(benchmark)

    for name, data in artifact_bytes.items():
        (output_dir / name).write_bytes(data)

    manifest = {
        "benchmark_id": fixture["benchmark_id"],
        "schema_version": fixture["schema_version"],
        "fixture_sha256": sha256(canonical_bytes(fixture)),
        "artifacts": {
            name: {"bytes": len(data), "sha256": sha256(data)}
            for name, data in sorted(artifact_bytes.items())
        },
        "model_calls": 0,
    }
    (output_dir / "manifest.json").write_bytes(canonical_line(manifest))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path(__file__).with_name("fixtures") / "a11_path_required_cases.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    fixture = load_fixture(args.fixture)
    manifest = write_artifacts(fixture, args.output_dir)
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
