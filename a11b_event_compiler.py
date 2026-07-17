#!/usr/bin/env python3
"""Pure zero-model A11b compiler for the registered T0/T1/E1 contrast.

The compiler consumes only one governed flat traversal plus a question-only
plan.  It derives one shared evidence object, adds identical temporal and
completeness aids to T1/E1, and adds reference-only event grouping to E1.  It
never reads efficacy labels, answerability gold, an arm-specific input, or a
model response.
"""

from __future__ import annotations

import copy
import datetime as dt
import re
from pathlib import Path
from typing import Any, Mapping

from a11_evidence_core import (
    REGISTERED_REFERENCE_PATHS,
    canonical_bytes,
    parse_relative_reference,
    resource_ref,
    sha256,
)


COMPILER_VERSION = "a11b-event-compiler-v1"
A11B_QUESTION_PLAN_VERSION = "a11b-question-plan-v1"
TEMPORAL_AIDS_VERSION = "a11b-temporal-aids-v1"
ANSWERABILITY_VERSION = "a11b-answerability-v1"

ARM_T0 = "t0_flat_traversal"
ARM_T1 = "t1_flat_traversal_with_aids"
ARM_E1 = "e1_event_groups_with_identical_aids"
ARMS = (ARM_T0, ARM_T1, ARM_E1)

_SOURCE_FIELDS = {"resources", "path_citations", "root_refs", "bounds"}
_PLAN_FIELDS = {
    "version",
    "question_sha256",
    "question_family",
    "temporal_policy",
    "path_signatures",
}
_FORBIDDEN_KEYS = {
    "audit",
    "arm",
    "arms",
    "answerable",
    "checker",
    "expected",
    "failure_mode",
    "gold",
    "governed",
    "reference_answer",
    "selected_terminal_id",
    "selected_root_ref",
    "true",
}
_FORBIDDEN_PREFIXES = (
    "audit_",
    "checker_",
    "expected_",
    "gold_",
    "governed_",
    "true_",
)
_FORBIDDEN_ARM_VALUES = {
    ARM_T0,
    ARM_T1,
    ARM_E1,
}
_REGISTERED_RELATIONS = {
    "Observation.hasMember",
    "Observation.specimen",
    "DiagnosticReport.result",
    "DiagnosticReport.specimen",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NAIVE_DATE_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$"
)
_INSTANT = re.compile(
    r"^\d{4}-\d{2}-\d{2}T(?P<hour>\d{2}):(?P<minute>\d{2}):"
    r"(?P<second>\d{2})(?:\.(?P<fraction>\d+))?"
    r"(?:Z|(?P<offset_sign>[+-])(?P<offset_hour>\d{2}):"
    r"(?P<offset_minute>\d{2}))$"
)


def plan_question(question: object) -> dict[str, Any]:
    """Derive the frozen A11b plan from raw question text only."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("A11b question is invalid")
    normalized = " ".join(question.lower().split())
    policies = [
        value
        for value in ("first", "latest")
        if re.search(rf"(?<![a-z0-9]){value}(?![a-z0-9])", normalized)
    ]
    if len(policies) != 1:
        raise ValueError("A11b question must select exactly one temporal policy")
    if not any(
        term in normalized
        for term in ("culture", "microbiolog", "organism", "specimen")
    ):
        raise ValueError("A11b question has no microbiology dispatcher term")
    root_relation = (
        "DiagnosticReport.result"
        if re.search(
            r"(?<![a-z0-9])diagnostic\s*report(?![a-z0-9])", normalized
        )
        else "Observation.hasMember"
    )
    terminal_relation = (
        "Observation.specimen"
        if "specimen" in normalized
        else "Observation.hasMember"
    )
    signature = [root_relation, terminal_relation]
    if "through an intermediate observation" in normalized:
        signature.insert(-1, "Observation.hasMember")
    return {
        "version": A11B_QUESTION_PLAN_VERSION,
        "question_sha256": sha256(question.encode("utf-8")),
        "question_family": "microbiology",
        "temporal_policy": policies[0],
        "path_signatures": [signature],
    }


def _reject_forbidden_input(value: Any, location: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError(f"forbidden compiler input at {location}")
            snake = re.sub(r"(?<!^)(?=[A-Z])", "_", key)
            normalized = re.sub(
                r"[^a-z0-9]+", "_", snake.lower()
            ).strip("_")
            if normalized in _FORBIDDEN_KEYS or normalized.startswith(
                _FORBIDDEN_PREFIXES
            ):
                raise ValueError(f"forbidden compiler input at {location}.{key}")
            _reject_forbidden_input(nested, f"{location}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_forbidden_input(nested, f"{location}[{index}]")
    elif isinstance(value, str):
        if value.strip().lower() in _FORBIDDEN_ARM_VALUES:
            raise ValueError(f"forbidden compiler input at {location}")
    elif value is not None and type(value) not in {bool, int, float}:
        raise ValueError(f"forbidden compiler input at {location}")


def _pointer_segments(pointer: object) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
        raise ValueError("governed traversal reference pointer is invalid")
    raw_segments = pointer[1:].split("/")
    if any(not segment or re.search(r"~(?![01])", segment) for segment in raw_segments):
        raise ValueError("governed traversal reference pointer is invalid")
    return [segment.replace("~1", "/").replace("~0", "~") for segment in raw_segments]


def _reference_object_at_pointer(
    resource: dict[str, Any], pointer: object
) -> tuple[dict[str, Any], list[str]]:
    segments = _pointer_segments(pointer)
    value: Any = resource
    try:
        for segment in segments:
            if isinstance(value, list):
                if not segment.isdigit() or (len(segment) > 1 and segment.startswith("0")):
                    raise ValueError
                value = value[int(segment)]
            elif isinstance(value, dict):
                value = value[segment]
            else:
                raise ValueError
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("governed traversal reference pointer is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("governed traversal pointer does not address a reference")
    return value, segments


def _validate_replayed_step(
    step: dict[str, Any], index: dict[str, dict[str, Any]]
) -> str | None:
    source_ref = step["source"]
    source = index.get(source_ref)
    if source is None:
        raise ValueError("governed traversal citation source is unavailable")
    reference_object, segments = _reference_object_at_pointer(
        source, step["json_pointer"]
    )
    requested_reference = reference_object.get("reference")
    target = step.get("target")
    if isinstance(requested_reference, str):
        if target is None:
            raise ValueError("unavailable traversal reference is not redacted")
        parsed = parse_relative_reference(requested_reference)
        if parsed is None:
            raise ValueError("governed traversal reference is not relative FHIR")
        target_type, target_id, requested_version = parsed
        canonical_target: str | None = f"{target_type}/{target_id}"
    elif (
        target is None
        and reference_object == {"display": "Reference withheld"}
        and isinstance(step.get("target_type"), str)
    ):
        target_type = step["target_type"]
        requested_version = None
        canonical_target = None
    else:
        raise ValueError("governed traversal pointer does not address a reference")
    shape = (
        "singular"
        if len(segments) == 1
        else (
            "repeating"
            if len(segments) == 2 and segments[1].isdigit()
            else "invalid"
        )
    )
    if (
        (source["resourceType"], segments[0], target_type, shape)
        not in REGISTERED_REFERENCE_PATHS
        or step.get("target_type") != target_type
    ):
        raise ValueError("governed traversal relation is unregistered")
    if target is not None and target != canonical_target:
        raise ValueError("governed traversal target does not match FHIR reference")
    if requested_version is not None:
        if step.get("requested_reference") != requested_reference:
            raise ValueError("versioned traversal request is not preserved")
        resolved = index.get(canonical_target)
        if target is not None and str(
            resolved.get("meta", {}).get("versionId", "") if resolved else ""
        ) != requested_version:
            raise ValueError("versioned traversal target changed")
    elif "requested_reference" in step:
        raise ValueError("unversioned traversal has a requested-reference override")
    return canonical_target


def _validate_inputs(
    source_packet: object, question: object, question_plan: object
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(source_packet, dict) or set(source_packet) != _SOURCE_FIELDS:
        raise ValueError("forbidden compiler input: source fields")
    if not isinstance(question_plan, dict) or set(question_plan) != _PLAN_FIELDS:
        raise ValueError("forbidden compiler input: question-plan fields")
    _reject_forbidden_input(source_packet, "source")
    _reject_forbidden_input(question_plan, "question_plan")
    if question_plan != plan_question(question):
        raise ValueError("question-only plan does not match the raw question")

    if (
        question_plan.get("version") != A11B_QUESTION_PLAN_VERSION
        or _SHA256.fullmatch(str(question_plan.get("question_sha256") or ""))
        is None
        or question_plan.get("question_family") != "microbiology"
        or question_plan.get("temporal_policy") not in {"first", "latest"}
    ):
        raise ValueError("question-only plan is invalid")
    signatures = question_plan.get("path_signatures")
    if (
        not isinstance(signatures, list)
        or not signatures
        or any(
            not isinstance(signature, list)
            or not signature
            or any(
                not isinstance(relation, str) or not relation
                or relation not in _REGISTERED_RELATIONS
                for relation in signature
            )
            for signature in signatures
        )
        or len({canonical_bytes(signature) for signature in signatures})
        != len(signatures)
    ):
        raise ValueError("question-only path signatures are invalid")

    resources = source_packet.get("resources")
    citations = source_packet.get("path_citations")
    root_refs = source_packet.get("root_refs")
    bounds = source_packet.get("bounds")
    if (
        not isinstance(resources, list)
        or not isinstance(citations, list)
        or not isinstance(root_refs, list)
        or len(root_refs) < 3
        or len(set(root_refs)) != len(root_refs)
        or not isinstance(bounds, dict)
        or not isinstance(bounds.get("outcomes"), list)
    ):
        raise ValueError("governed traversal is invalid")
    index: dict[str, dict[str, Any]] = {}
    for resource in resources:
        if not isinstance(resource, dict):
            raise ValueError("governed traversal resource is invalid")
        try:
            reference = resource_ref(resource)
        except (KeyError, TypeError) as exc:
            raise ValueError("governed traversal resource is invalid") from exc
        if reference in index:
            raise ValueError("governed traversal has duplicate resources")
        index[reference] = resource
    if any(not isinstance(ref, str) or ref not in index for ref in root_refs):
        raise ValueError("governed traversal root is unavailable")
    if any(index[ref].get("resourceType") not in {"Observation", "DiagnosticReport"} for ref in root_refs):
        raise ValueError("governed traversal root type is invalid")
    if any(not isinstance(outcome, str) for outcome in bounds["outcomes"]):
        raise ValueError("governed traversal bound outcome is invalid")
    for citation in citations:
        if (
            not isinstance(citation, dict)
            or citation.get("state") not in {"available", "unavailable"}
            or not isinstance(citation.get("steps"), list)
            or not citation["steps"]
        ):
            raise ValueError("governed traversal citation is invalid")
        if not isinstance(citation["steps"][0], dict):
            raise ValueError("governed traversal citation step is invalid")
        expected_source = citation["steps"][0].get("source")
        for index_in_path, step in enumerate(citation["steps"]):
            if (
                not isinstance(step, dict)
                or not isinstance(step.get("source"), str)
                or not isinstance(step.get("json_pointer"), str)
                or not isinstance(step.get("target_type"), str)
                or (
                    step.get("target") is not None
                    and not isinstance(step.get("target"), str)
                )
            ):
                raise ValueError("governed traversal citation step is invalid")
            if step["source"] != expected_source:
                raise ValueError("governed traversal citation chain is invalid")
            canonical_target = _validate_replayed_step(step, index)
            target = step.get("target")
            if target is None:
                if index_in_path != len(citation["steps"]) - 1:
                    raise ValueError("governed traversal citation chain is invalid")
                expected_source = None
            else:
                if target != canonical_target or target not in index:
                    raise ValueError("governed traversal citation target is unavailable")
                expected_source = target
        final_target = citation["steps"][-1].get("target")
        if (
            citation.get("target") != final_target
            or citation.get("target_type")
            != citation["steps"][-1].get("target_type")
            or (citation["state"] == "available") != (final_target is not None)
        ):
            raise ValueError("governed traversal citation terminal state is invalid")
    return copy.deepcopy(source_packet), copy.deepcopy(question_plan)


def _normalized_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


def _parse_exact_instant(value: object) -> tuple[dt.datetime | None, str | None]:
    if not isinstance(value, str) or not value:
        return None, "clinical_time_missing"
    if _DATE_ONLY.fullmatch(value):
        return None, "precision_ambiguous"
    if _NAIVE_DATE_TIME.fullmatch(value):
        return None, "timezone_missing"
    match = _INSTANT.fullmatch(value)
    if match is None:
        return None, "invalid_clinical_time"
    fraction = match.group("fraction") or ""
    if len(fraction) > 6:
        return None, "unsupported_fractional_precision"
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second"))
    offset_hour = int(match.group("offset_hour") or 0)
    offset_minute = int(match.group("offset_minute") or 0)
    if (
        hour > 23
        or minute > 59
        or second > 59
        or offset_hour > 14
        or offset_minute > 59
        or (offset_hour == 14 and offset_minute != 0)
    ):
        return None, "invalid_clinical_time"
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, "invalid_clinical_time"
    if parsed.tzinfo is None:
        return None, "timezone_missing"
    return parsed.astimezone(dt.UTC), None


def _event_interval(root: dict[str, Any]) -> dict[str, Any]:
    choices = {
        "effectiveDateTime": root.get("effectiveDateTime"),
        "effectivePeriod": root.get("effectivePeriod"),
        "effectiveInstant": root.get("effectiveInstant"),
        "effectiveTiming": root.get("effectiveTiming"),
    }
    present = [key for key, value in choices.items() if value not in (None, "", {})]
    if not present:
        return {"status": "ambiguous", "reason": "clinical_time_missing"}
    if len(present) != 1:
        return {"status": "ambiguous", "reason": "conflicting_effective_fields"}
    choice = present[0]
    if choice not in {"effectiveDateTime", "effectivePeriod"}:
        return {"status": "ambiguous", "reason": "unsupported_effective_type"}
    source = f"{root['resourceType']}.{choice}"
    if choice == "effectiveDateTime":
        instant, reason = _parse_exact_instant(choices[choice])
        if reason is not None:
            return {"status": "ambiguous", "reason": reason, "source": source}
        assert instant is not None
        normalized = _normalized_utc(instant)
        return {
            "status": "exact",
            "source": source,
            "start_utc": normalized,
            "end_utc": normalized,
            "_start": instant,
            "_end": instant,
        }

    period = choices[choice]
    if not isinstance(period, dict):
        return {"status": "ambiguous", "reason": "invalid_effective_period", "source": source}
    if not isinstance(period.get("start"), str) or not isinstance(
        period.get("end"), str
    ):
        return {"status": "ambiguous", "reason": "open_effective_period", "source": source}
    start, start_reason = _parse_exact_instant(period["start"])
    end, end_reason = _parse_exact_instant(period["end"])
    if start_reason is not None or end_reason is not None:
        return {
            "status": "ambiguous",
            "reason": start_reason or end_reason,
            "source": source,
        }
    assert start is not None and end is not None
    if start > end:
        return {"status": "ambiguous", "reason": "invalid_effective_period", "source": source}
    return {
        "status": "exact",
        "source": source,
        "start_utc": _normalized_utc(start),
        "end_utc": _normalized_utc(end),
        "_start": start,
        "_end": end,
    }


def _relation(step: dict[str, Any]) -> str:
    source = step.get("source")
    pointer = step.get("json_pointer")
    if not isinstance(source, str) or not isinstance(pointer, str):
        return ""
    segments = [segment for segment in pointer.split("/") if segment]
    if not segments:
        return ""
    field = segments[0].replace("~1", "/").replace("~0", "~")
    return f"{source.split('/', 1)[0]}.{field}"


def _root_citations(
    citations: list[dict[str, Any]], root_ref: str
) -> list[dict[str, Any]]:
    return [
        citation
        for citation in citations
        if isinstance(citation.get("steps"), list)
        and citation["steps"]
        and citation["steps"][0].get("source") == root_ref
    ]


def _requirements(
    citations: list[dict[str, Any]],
    root_ref: str,
    signatures: list[list[str]],
    index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rooted = _root_citations(citations, root_ref)

    def satisfies(citation: dict[str, Any], signature: list[str]) -> bool:
        if citation.get("state") != "available" or len(citation["steps"]) != len(
            signature
        ):
            return False
        expected_source = root_ref
        for step, relation in zip(citation["steps"], signature, strict=True):
            target = step.get("target")
            if (
                step.get("source") != expected_source
                or _relation(step) != relation
                or not isinstance(target, str)
                or target not in index
                or step.get("target_type") != target.split("/", 1)[0]
            ):
                return False
            expected_source = target
        return citation.get("target") == expected_source

    return [
        {
            "path": signature,
            "satisfied": any(
                satisfies(citation, signature) for citation in rooted
            ),
        }
        for signature in signatures
    ]


def _temporal_aids(
    source: dict[str, Any], plan: dict[str, Any], index: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    citations = source["path_citations"]
    signatures = plan["path_signatures"]
    events = []
    for root_ref in sorted(source["root_refs"]):
        interval = _event_interval(index[root_ref])
        events.append(
            {
                "root_ref": root_ref,
                "event_time": interval,
                "requirements": _requirements(
                    citations, root_ref, signatures, index
                ),
                "ordinal": None,
                "selected": False,
            }
        )

    reason = None
    if source["bounds"]["outcomes"]:
        reason = "evidence_incomplete"
    else:
        invalid = [
            event["event_time"]["reason"]
            for event in events
            if event["event_time"]["status"] != "exact"
        ]
        if invalid:
            reason = invalid[0]

    ordered = sorted(events, key=lambda event: event["root_ref"])
    selected = None
    if reason is None:
        endpoint_field = (
            "_start" if plan["temporal_policy"] == "first" else "_end"
        )
        ordered = sorted(
            events,
            key=lambda event: (
                event["event_time"][endpoint_field],
                event["root_ref"],
            ),
        )
        endpoints = sorted(
            {event["event_time"][endpoint_field] for event in ordered}
        )
        for event in ordered:
            endpoint = event["event_time"][endpoint_field]
            event["ordinal"] = endpoints.index(endpoint) + 1
            event["tie_count"] = sum(
                candidate["event_time"][endpoint_field] == endpoint
                for candidate in ordered
            )
            event["total_distinct_ranks"] = len(endpoints)
        extreme = endpoints[0] if plan["temporal_policy"] == "first" else endpoints[-1]
        candidates = [
            event
            for event in ordered
            if event["event_time"][endpoint_field] == extreme
        ]
        if len(candidates) != 1:
            reason = (
                "temporal_tie"
                if all(
                    event["event_time"]["_start"]
                    == event["event_time"]["_end"]
                    for event in candidates
                )
                else "temporal_overlap"
            )
        else:
            selected = candidates[0]
            selected["selected"] = True
            if not all(
                item["satisfied"] for item in selected["requirements"]
            ):
                reason = "selected_path_incomplete"

    public_events = copy.deepcopy(ordered)
    for event in public_events:
        event["event_time"].pop("_start", None)
        event["event_time"].pop("_end", None)
    answerability = {
        "version": ANSWERABILITY_VERSION,
        "plan_sha256": sha256(canonical_bytes(plan)),
        "state": "sufficient" if reason is None else "insufficient",
        "selected_event_count": 1 if selected is not None else 0,
        "reason": reason or "requirements_satisfied",
    }
    return {
        "version": TEMPORAL_AIDS_VERSION,
        "temporal_policy": plan["temporal_policy"],
        "effective_period_endpoint": (
            "start" if plan["temporal_policy"] == "first" else "end"
        ),
        "events": public_events,
        "answerability_receipt": answerability,
    }


def _event_groups(
    source: dict[str, Any], aids: dict[str, Any], index: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    groups = []
    for event in aids["events"]:
        root_ref = event["root_ref"]
        rooted = _root_citations(source["path_citations"], root_ref)
        members: dict[str, int] = {}
        edges: dict[bytes, dict[str, Any]] = {}
        for citation in rooted:
            for depth, step in enumerate(citation["steps"], start=1):
                target = step.get("target")
                available = isinstance(target, str) and target in index
                if available:
                    members[target] = min(depth, members.get(target, depth))
                edge = {
                    "source": step.get("source"),
                    "target": target if available else None,
                    "target_type": step.get("target_type"),
                    "relation": _relation(step),
                    "json_pointer": step.get("json_pointer"),
                    "depth": depth,
                    "state": "available" if available else "unavailable",
                }
                edges[canonical_bytes(edge)] = edge
        groups.append(
            {
                "root_ref": root_ref,
                "member_refs": [
                    {"reference": ref, "depth": members[ref]}
                    for ref in sorted(members, key=lambda ref: (members[ref], ref))
                ],
                "typed_edges": [edges[key] for key in sorted(edges)],
            }
        )
    return groups


def compile_arms(
    source_packet: object,
    question: object,
    question_plan: object,
    *,
    max_packet_bytes: int,
) -> dict[str, Any]:
    """Compile byte-auditable T0/T1/E1 payloads without an arm-specific input."""

    if type(max_packet_bytes) is not int or max_packet_bytes <= 0:
        raise ValueError("registered packet bound is invalid")
    source, plan = _validate_inputs(source_packet, question, question_plan)
    resources = sorted(source["resources"], key=lambda item: resource_ref(item))
    citations = sorted(source["path_citations"], key=canonical_bytes)
    evidence = {"resources": resources, "path_citations": citations}
    index = {resource_ref(resource): resource for resource in resources}
    aids = _temporal_aids(source, plan, index)

    arms = {
        ARM_T0: {
            "schema_version": COMPILER_VERSION,
            "evidence": copy.deepcopy(evidence),
        },
        ARM_T1: {
            "schema_version": COMPILER_VERSION,
            "evidence": copy.deepcopy(evidence),
            "temporal_aids": copy.deepcopy(aids),
        },
        ARM_E1: {
            "schema_version": COMPILER_VERSION,
            "evidence": copy.deepcopy(evidence),
            "temporal_aids": copy.deepcopy(aids),
            "event_groups": _event_groups(source, aids, index),
        },
    }
    arm_payloads = {arm: canonical_bytes(payload) for arm, payload in arms.items()}
    if any(len(payload) > max_packet_bytes for payload in arm_payloads.values()):
        raise ValueError("registered packet bound exceeded")
    evidence_sha256 = sha256(canonical_bytes(evidence))
    paths_sha256 = sha256(canonical_bytes(evidence["path_citations"]))
    selection_sha256 = sha256(canonical_bytes(aids["events"]))
    answerability_sha256 = sha256(
        canonical_bytes(aids["answerability_receipt"])
    )
    return {
        "schema_version": COMPILER_VERSION,
        "arms": arms,
        "equivalence_receipt": {
            "evidence_sha256": evidence_sha256,
            "path_citations_sha256": paths_sha256,
            "event_selection_sha256": selection_sha256,
            "answerability_receipt_sha256": answerability_sha256,
            "arm_evidence_sha256": {
                arm: sha256(canonical_bytes(payload["evidence"]))
                for arm, payload in arms.items()
            },
            "arm_path_citations_sha256": {
                arm: sha256(canonical_bytes(payload["evidence"]["path_citations"]))
                for arm, payload in arms.items()
            },
            "arm_payloads": {
                arm: {"sha256": sha256(payload), "bytes": len(payload)}
                for arm, payload in arm_payloads.items()
            },
            "max_packet_bytes": max_packet_bytes,
            "model_calls": 0,
        },
    }


def compiler_receipt() -> dict[str, Any]:
    """Return the local source receipt used by future sealed builders."""

    payload = Path(__file__).read_bytes()
    return {"version": COMPILER_VERSION, "sha256": sha256(payload), "bytes": len(payload)}
