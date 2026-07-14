#!/usr/bin/env python3
"""Zero-model A11 gate for path retrieval plus event-group compilation.

The three synthetic mechanism proxies separate a promoted-recipe-shaped star,
flat bounded traversal, and a deterministic event-group projection. A sealed
product-packet adapter is still required before efficacy. No answer or judge
model is invoked by this module.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from a11_evidence_core import (
    apply_packet_byte_bound,
    canonical_bytes,
    canonical_line,
    project_star,
    project_traversal,
    resource_ref,
    sha256,
)


ARM_VOCABULARY_STAR = "promoted_recipe_shaped_star_proxy"
ARM_FLAT_TRAVERSAL = "promoted_recipe_shaped_flat_traversal_proxy"
ARM_EVENT_GROUP = "promoted_recipe_shaped_event_group_proxy"
ARMS = (ARM_VOCABULARY_STAR, ARM_FLAT_TRAVERSAL, ARM_EVENT_GROUP)

EVENT_GROUP_COMPILER_VERSION = "a11-event-group-v1"
ANSWERABILITY_VERSION = "a11-answerability-v1"
QUESTION_PLANNER_VERSION = "a11-question-plan-v1"

_SUMMARY_FIELDS = (
    "status",
    "category",
    "code",
    "valueQuantity",
    "valueCodeableConcept",
    "valueString",
    "interpretation",
    "effectiveDateTime",
    "effectivePeriod",
    "type",
    "collection",
)


def plan_question(question: str) -> dict[str, Any]:
    """Derive the event policy and path shape from question text only."""

    normalized = " ".join(question.lower().split())
    policies = [policy for policy in ("first", "latest") if policy in normalized]
    if len(policies) != 1:
        raise ValueError("question must select exactly one temporal policy")
    if "specimen" in normalized:
        path_signatures = [["Observation.hasMember", "Observation.specimen"]]
    elif any(token in normalized for token in ("organism", "finding", "gram stain")):
        path_signatures = [["Observation.hasMember", "Observation.hasMember"]]
    else:
        raise ValueError("question has no registered microbiology path plan")
    return {
        "version": QUESTION_PLANNER_VERSION,
        "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "question_family": "microbiology",
        "temporal_policy": policies[0],
        "path_signatures": path_signatures,
    }


def load_fixture(path: Path) -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if fixture.get("schema_version") != EVENT_GROUP_COMPILER_VERSION:
        raise ValueError("unsupported A11 event-group fixture schema")
    if fixture.get("promoted_baseline_recipe") != "qt4-vocabulary-promoted-v1":
        raise ValueError("fixture does not use the promoted vocabulary recipe")

    temporal_cases = 0
    case_ids: set[str] = set()
    for case in fixture["cases"]:
        case_id = case.get("case_id")
        if not case_id or case_id in case_ids:
            raise ValueError(f"duplicate or missing case id: {case_id}")
        case_ids.add(case_id)
        plan = plan_question(case.get("question", ""))
        sealed_plan = case.get("sealed_question_plan")
        if sealed_plan != plan:
            raise ValueError(f"question plan is absent or stale: {case_id}")
        temporal_cases += len(case.get("seed_refs", [])) >= 2
        if case.get("answerable") and not case.get("expected_event_root"):
            raise ValueError(f"answerable case has no expected event root: {case_id}")
        if case.get("answerable") and not case.get("expected_evidence_refs"):
            raise ValueError(f"answerable case has no expected evidence: {case_id}")
        allowed_purposes = case.get("allowed_purposes")
        if not isinstance(allowed_purposes, list) or not allowed_purposes:
            raise ValueError(f"case has no allowed purposes: {case_id}")
        if not isinstance(case.get("max_targets"), int) or case["max_targets"] < 1:
            raise ValueError(f"case has invalid max targets: {case_id}")
        if not isinstance(case.get("max_packet_bytes"), int) or case["max_packet_bytes"] < 1:
            raise ValueError(f"case has invalid packet byte bound: {case_id}")
        keys: set[tuple[str, str]] = set()
        for entry in case.get("resources", []):
            key = (entry.get("practice_id"), resource_ref(entry.get("resource", {})))
            if key in keys:
                raise ValueError(f"duplicate resource key in {case_id}: {key}")
            keys.add(key)
        if case.get("answerable") and case.get("minimum_evidence_hops", 0) < 2:
            raise ValueError(f"answerable case is not path-required: {case_id}")
    if not case_ids:
        raise ValueError("fixture has no cases")
    if temporal_cases < 2:
        raise ValueError("fixture must exercise temporal ranking with multiple roots")
    if not any(case["answerable"] for case in fixture["cases"]):
        raise ValueError("fixture has no answerable cases")
    return fixture


def _parse_instant(value: str) -> float:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.timestamp()


def canonical_event_time(resource: dict[str, Any]) -> tuple[str, str]:
    """Return the clinical event time and the exact FHIR field that supplied it."""

    resource_type = resource.get("resourceType")
    candidates: list[tuple[str, Any]] = []
    if resource_type == "Observation":
        candidates.extend(
            [
                ("Observation.effectiveDateTime", resource.get("effectiveDateTime")),
                ("Observation.effectivePeriod.end", resource.get("effectivePeriod", {}).get("end")),
                ("Observation.effectivePeriod.start", resource.get("effectivePeriod", {}).get("start")),
            ]
        )
    elif resource_type == "DiagnosticReport":
        candidates.extend(
            [
                ("DiagnosticReport.effectiveDateTime", resource.get("effectiveDateTime")),
                ("DiagnosticReport.effectivePeriod.end", resource.get("effectivePeriod", {}).get("end")),
                ("DiagnosticReport.effectivePeriod.start", resource.get("effectivePeriod", {}).get("start")),
            ]
        )
    for source, value in candidates:
        if isinstance(value, str) and value:
            _parse_instant(value)
            return value, source
    raise ValueError(f"event root has no canonical clinical time: {resource_ref(resource)}")


def _summary(resource: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "reference": resource_ref(resource),
        "resource_type": resource["resourceType"],
    }
    version = resource.get("meta", {}).get("versionId")
    if version is not None:
        summary["version_id"] = str(version)
    for field in _SUMMARY_FIELDS:
        if field in resource:
            summary[field] = resource[field]
    return summary


def _relation(source_ref: str, json_pointer: str) -> str:
    source_type = source_ref.split("/", 1)[0]
    segments = [segment for segment in json_pointer.split("/") if segment]
    field = segments[0].replace("~1", "/").replace("~0", "~") if segments else "reference"
    return f"{source_type}.{field}"


def _citation_relations(citation: dict[str, Any]) -> list[str]:
    return [_relation(step["source"], step["json_pointer"]) for step in citation["steps"]]


def _selected_ordinal(policy: str, total: int) -> int:
    return 1 if policy == "first" else total


def compile_event_groups(
    source_packet: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    packet_resources = {
        resource_ref(resource): resource for resource in source_packet["resources"]
    }
    roots: list[tuple[float, str, str, str]] = []
    missing_clinical_time = False
    for seed_ref in source_packet["root_refs"]:
        root = packet_resources.get(seed_ref)
        if root is None:
            continue
        try:
            event_time, time_source = canonical_event_time(root)
        except ValueError:
            missing_clinical_time = True
            continue
        roots.append((_parse_instant(event_time), seed_ref, event_time, time_source))
    roots.sort(key=lambda item: (item[0], item[1]))

    groups: list[dict[str, Any]] = []
    total = len(roots)
    source_incomplete = bool(source_packet["bounds"].get("outcomes"))
    selected_ordinal = (
        0
        if missing_clinical_time or source_incomplete
        else _selected_ordinal(plan["temporal_policy"], total)
    )
    selected_requirement_states: list[bool] = []
    for ordinal, (_, seed_ref, event_time, time_source) in enumerate(roots, start=1):
        citations = [
            citation
            for citation in source_packet["path_citations"]
            if citation.get("steps") and citation["steps"][0]["source"] == seed_ref
        ]
        member_depth: dict[str, int] = {}
        edge_rows: dict[bytes, dict[str, Any]] = {}
        for citation in citations:
            for depth, step in enumerate(citation["steps"], start=1):
                target = step.get("target")
                available = target is not None and target in packet_resources
                if available:
                    member_depth[target] = min(depth, member_depth.get(target, depth))
                edge = {
                    "source": step["source"],
                    "target": target if available else None,
                    "target_type": step["target_type"],
                    "relation": _relation(step["source"], step["json_pointer"]),
                    "json_pointer": step["json_pointer"],
                    "depth": depth,
                    "state": "available" if available else "unavailable",
                }
                if available:
                    edge["requested_reference"] = step.get(
                        "requested_reference", target
                    )
                edge_rows[canonical_bytes(edge)] = edge

        requirements = []
        for signature in plan["path_signatures"]:
            satisfied = any(
                citation["state"] == "available"
                and _citation_relations(citation) == signature
                for citation in citations
            )
            requirements.append({"path": signature, "satisfied": satisfied})
        selected = ordinal == selected_ordinal
        if selected:
            selected_requirement_states.extend(item["satisfied"] for item in requirements)
        groups.append(
            {
                "root": _summary(packet_resources[seed_ref]),
                "canonical_event_time": event_time,
                "canonical_event_time_source": time_source,
                "temporal_rank": {
                    "ordinal": ordinal,
                    "total": total,
                    "is_first": ordinal == 1,
                    "is_latest": ordinal == total,
                    "selected_for_question": selected,
                },
                "members": [
                    {
                        "depth": member_depth[ref],
                        "resource": _summary(packet_resources[ref]),
                    }
                    for ref in sorted(member_depth, key=lambda ref: (member_depth[ref], ref))
                ],
                "typed_edges": [edge_rows[key] for key in sorted(edge_rows)],
                "requirements": requirements,
            }
        )

    sufficient = (
        not missing_clinical_time
        and not source_incomplete
        and bool(groups)
        and bool(selected_requirement_states)
        and all(selected_requirement_states)
    )
    model_packet = {
        "event_groups": groups,
        "answerability_receipt": {
            "version": ANSWERABILITY_VERSION,
            "question_plan": plan,
            "state": "sufficient" if sufficient else "insufficient",
            **(
                {"reason": "clinical_time_missing"}
                if missing_clinical_time
                else ({"reason": "evidence_incomplete"} if source_incomplete else {})
            ),
            "selected_group_count": sum(
                group["temporal_rank"]["selected_for_question"] for group in groups
            ),
        },
    }
    return model_packet


def _packet_refs(model_packet: dict[str, Any]) -> set[str]:
    if "resources" in model_packet:
        return {resource_ref(resource) for resource in model_packet["resources"]}
    refs: set[str] = set()
    for group in model_packet.get("event_groups", []):
        refs.add(group["root"]["reference"])
        refs.update(member["resource"]["reference"] for member in group["members"])
    return refs


def _retrieval_receipt(source_packet: dict[str, Any]) -> dict[str, Any]:
    resources = source_packet["resources"]
    citations = source_packet["audit_path_citations"]
    root_refs = source_packet["root_refs"]
    resource_refs = sorted(resource_ref(resource) for resource in resources)
    model_retrieval = {
        "root_refs": root_refs,
        "resources": resources,
        "path_citations": citations,
    }
    return {
        "source_sha256": sha256(canonical_bytes(model_retrieval)),
        "root_refs_sha256": sha256(canonical_bytes(root_refs)),
        "resource_refs_sha256": sha256(canonical_bytes(resource_refs)),
        "path_citations_sha256": sha256(canonical_bytes(citations)),
        "bounds_sha256": sha256(canonical_bytes(source_packet["bounds"])),
    }


def compile_case(case: dict[str, Any], arm: str) -> dict[str, Any]:
    if arm not in ARMS:
        raise ValueError(f"unknown arm: {arm}")
    plan = plan_question(case["question"])
    if arm == ARM_VOCABULARY_STAR:
        source_packet = project_star(case)
        model_packet = {
            "resources": source_packet["resources"],
            "path_citations": [],
        }
    else:
        source_packet = project_traversal(case)
        model_packet = (
            {
                "resources": source_packet["resources"],
                "path_citations": source_packet["path_citations"],
            }
            if arm == ARM_FLAT_TRAVERSAL
            else compile_event_groups(source_packet, plan)
        )

    model_packet, byte_bound_outcomes = apply_packet_byte_bound(
        case, model_packet, arm=arm
    )

    retrieval_receipt = _retrieval_receipt(source_packet)

    refs = _packet_refs(model_packet)
    expected = set(case["expected_evidence_refs"])
    forbidden = set(case.get("forbidden_resource_refs", []))
    leakage_count = len(refs & forbidden)
    evidence_recall = (
        len(refs & expected) / len(expected) if case["answerable"] else None
    )

    if arm == ARM_VOCABULARY_STAR:
        mechanism_success = leakage_count == 0 and (
            refs.isdisjoint(expected) if case["answerable"] else True
        )
    elif arm == ARM_FLAT_TRAVERSAL:
        planned_signatures = plan["path_signatures"]
        deep_targets = {
            citation["resolved_target"]
            for citation in source_packet["audit_path_citations"]
            if citation["state"] == "available"
            and len(citation["steps"]) >= case["minimum_evidence_hops"]
            and _citation_relations(citation) in planned_signatures
        }
        mechanism_success = (
            leakage_count == 0
            and (
                expected <= refs and expected <= deep_targets
                if case["answerable"]
                else set(case["expected_unavailable_refs"])
                <= {
                    citation["requested_target"]
                    for citation in source_packet["audit_path_citations"]
                    if citation["state"] == "unavailable"
                    and _citation_relations(citation) in planned_signatures
                }
            )
        )
    else:
        receipt_state = model_packet["answerability_receipt"]["state"]
        selected_roots = {
            group["root"]["reference"]
            for group in model_packet.get("event_groups", [])
            if group["temporal_rank"]["selected_for_question"]
        }
        mechanism_success = (
            leakage_count == 0
            and (
                expected <= refs
                and receipt_state == "sufficient"
                and selected_roots == {case["expected_event_root"]}
                if case["answerable"]
                else receipt_state == "insufficient"
            )
        )

    return {
        "case_id": case["case_id"],
        "arm": arm,
        "answerable": case["answerable"],
        "mechanism_success": mechanism_success,
        "evidence_recall": evidence_recall,
        "authorization_leakage_count": leakage_count,
        "packet_bytes": len(canonical_bytes(model_packet)),
        "retrieval_receipt": retrieval_receipt,
        "bound_outcomes": (
            sorted(
                set(source_packet["bounds"].get("outcomes", []))
                | set(byte_bound_outcomes)
            )
        ),
        "model_packet": model_packet,
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
        aggregates[arm] = {
            "cases": len(arm_results),
            "mechanism_successes": sum(result["mechanism_success"] for result in arm_results),
            "answerable_evidence_recall": sum(
                result["evidence_recall"] or 0 for result in answerable
            )
            / len(answerable),
            "authorization_leakage_count": sum(
                result["authorization_leakage_count"] for result in arm_results
            ),
            "packet_bytes": sum(result["packet_bytes"] for result in arm_results),
        }
    return {
        "benchmark_id": fixture["benchmark_id"],
        "schema_version": fixture["schema_version"],
        "promoted_baseline_recipe": fixture["promoted_baseline_recipe"],
        "results": results,
        "aggregates": aggregates,
    }


def write_artifacts(fixture: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    benchmark = run_benchmark(fixture)
    artifacts: dict[str, bytes] = {}
    for arm in ARMS:
        rows = [result for result in benchmark["results"] if result["arm"] == arm]
        artifacts[f"{arm}.jsonl"] = b"".join(canonical_line(row) for row in rows)
    artifacts["mechanism-results.json"] = canonical_line(benchmark)
    for name, data in artifacts.items():
        (output_dir / name).write_bytes(data)
    manifest = {
        "benchmark_id": fixture["benchmark_id"],
        "schema_version": fixture["schema_version"],
        "fixture_sha256": sha256(canonical_bytes(fixture)),
        "compiler_sha256": sha256(Path(__file__).read_bytes()),
        "compiler_dependencies": {
            name: sha256(Path(__file__).with_name(name).read_bytes())
            for name in (
                "a11_event_group_benchmark.py",
                "a11_evidence_core.py",
            )
        },
        "artifacts": {
            name: {"bytes": len(data), "sha256": sha256(data)}
            for name, data in sorted(artifacts.items())
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
        default=Path(__file__).with_name("fixtures") / "a11_event_group_cases.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    fixture = load_fixture(args.fixture)
    print(json.dumps(write_artifacts(fixture, args.output_dir), sort_keys=True))


if __name__ == "__main__":
    main()
