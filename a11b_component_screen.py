"""Compile zero-model A11b component-screen packets from governed inputs."""

from __future__ import annotations

import copy
from typing import Any

from a11_evidence_core import canonical_bytes, sha256
from a11b_event_compiler import ARM_E1, ARM_T0, ARM_T1, compile_arms


SCREEN_VERSION = "a11b-component-screen-v1"
ARM_NAMES = (
    "t0",
    "temporal_rank_only",
    "selected_marker_only",
    "answerability_receipt_bundle",
    "path_only",
    "group_only",
    "t0_byte_matched_placebo",
)
_RANK_FIELDS = (
    "root_ref",
    "ordinal",
    "tie_count",
    "total_distinct_ranks",
)


def _rank_only(aids: dict[str, Any]) -> dict[str, Any]:
    events = []
    for event in aids["events"]:
        events.append(
            {
                field: copy.deepcopy(event[field])
                for field in _RANK_FIELDS
                if field in event
            }
        )
    return {
        "version": aids["version"],
        "temporal_policy": aids.get("temporal_policy"),
        "effective_period_endpoint": aids.get("effective_period_endpoint"),
        "events": events,
    }


def _selected_markers(aids: dict[str, Any]) -> dict[str, Any]:
    events = []
    selected_count = 0
    for event in aids["events"]:
        if event["selected"]:
            selected_count += 1
        events.append(
            {"root_ref": event["root_ref"], "selected": event["selected"]}
        )
    if selected_count > 1:
        raise ValueError("component screen has multiple selected events")
    return {"version": aids["version"], "events": events}


def _size_matched_placebo(
    *, evidence: dict[str, Any], target_bytes: int
) -> dict[str, Any]:
    payload = {
        "schema_version": SCREEN_VERSION,
        "evidence": copy.deepcopy(evidence),
        "placebo_control": "",
    }
    padding = target_bytes - len(canonical_bytes(payload))
    if padding < 0:
        raise ValueError("path-only packet is too small for the placebo envelope")
    payload["placebo_control"] = "x" * padding
    if len(canonical_bytes(payload)) != target_bytes:
        raise ValueError("placebo packet could not be byte matched")
    return payload


def compile_component_screen(
    source_packet: object,
    question: object,
    question_plan: object,
    *,
    max_packet_bytes: int,
) -> dict[str, Any]:
    """Return isolated aids, representation controls, and a byte-matched placebo."""

    if type(max_packet_bytes) is not int or max_packet_bytes <= 0:
        raise ValueError("component screen packet bound is invalid")
    compiled = compile_arms(
        source_packet,
        question,
        question_plan,
        max_packet_bytes=max_packet_bytes,
    )
    evidence = copy.deepcopy(compiled["arms"][ARM_T0]["evidence"])
    aids = copy.deepcopy(compiled["arms"][ARM_T1]["temporal_aids"])
    groups = copy.deepcopy(compiled["arms"][ARM_E1]["event_groups"])
    common = {"schema_version": SCREEN_VERSION}
    t0 = {**common, "evidence": copy.deepcopy(evidence)}
    path_only = {
        **common,
        "evidence": copy.deepcopy(evidence),
        "temporal_aids": copy.deepcopy(aids),
    }
    arms = {
        "t0": t0,
        "temporal_rank_only": {
            **common,
            "evidence": copy.deepcopy(evidence),
            "temporal_aids": _rank_only(aids),
        },
        "selected_marker_only": {
            **common,
            "evidence": copy.deepcopy(evidence),
            "temporal_aids": _selected_markers(aids),
        },
        "answerability_receipt_bundle": {
            **common,
            "evidence": copy.deepcopy(evidence),
            "temporal_aids": {
                "version": aids["version"],
                "answerability_receipt": copy.deepcopy(
                    aids["answerability_receipt"]
                ),
            },
        },
        "path_only": path_only,
        "group_only": {
            **common,
            "evidence": {"resources": copy.deepcopy(evidence["resources"])},
            "temporal_aids": copy.deepcopy(aids),
            "event_groups": groups,
        },
    }
    arms["t0_byte_matched_placebo"] = _size_matched_placebo(
        evidence=evidence,
        target_bytes=len(canonical_bytes(path_only)),
    )
    payloads = {name: canonical_bytes(payload) for name, payload in arms.items()}
    if set(arms) != set(ARM_NAMES) or any(
        len(payload) > max_packet_bytes for payload in payloads.values()
    ):
        raise ValueError("component screen packet bound exceeded")
    return {
        "schema_version": SCREEN_VERSION,
        "arms": arms,
        "receipt": {
            "arms": {
                name: {"bytes": len(payloads[name]), "sha256": sha256(payloads[name])}
                for name in ARM_NAMES
            },
            "upstream_compiler_receipt_sha256": sha256(
                canonical_bytes(compiled["equivalence_receipt"])
            ),
            "max_packet_bytes": max_packet_bytes,
            "model_calls": 0,
        },
    }
