"""Unsealed grading boundary for the next A11b successor.

This module is intentionally separate from :mod:`a11b_grading`, whose bytes
are bound into the completed r3 controller.  It is development groundwork, not
an authorized live-run component; a future controller must snapshot this file,
the answer contract, and the safety-evidence producer together.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import a11b_answer_contract as contract
import a11b_grading as legacy


ANALYSIS_VERSION = "a11b-successor-analysis-v1-unsealed"
SAFETY_EVIDENCE_VERSION = "a11b-successor-critical-safety-v1"
TEMPORAL_METRIC_VERSION = "selected_event_value_path_all_strata_v1"
CRITICAL_METRICS = (
    "unsupported_answers",
    "citation_failures",
    "temporal_binding_errors",
)


def deterministic_partition(
    *,
    question: Mapping[str, Any],
    gold: Mapping[str, Any],
    answer: Mapping[str, Any],
) -> tuple[int | None, dict[str, Any] | None]:
    """Grade categorical answer state without inferring it from answer prose."""

    validated = contract.validate_answer(answer)
    legacy_answer = {
        "answer": (
            "Insufficient evidence."
            if validated["status"] == contract.INSUFFICIENT
            else validated["answer"]
        ),
        "source_resource_ids": (
            []
            if validated["status"] == contract.INSUFFICIENT
            else list(validated["source_resource_ids"])
        ),
        "evidence_summary": validated["evidence_summary"],
        "insufficiency_reason": validated["insufficiency_reason"],
    }
    verdict, panel_item = legacy.deterministic_partition(
        question=question,
        gold=gold,
        answer=legacy_answer,
    )
    if panel_item is None:
        return verdict, None
    return verdict, {
        **panel_item,
        "status": validated["status"],
        "source_resource_ids": list(validated["source_resource_ids"]),
        "evidence_summary": validated["evidence_summary"],
    }


def validate_safety_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an explicit all-strata safety receipt for successor assembly."""

    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "temporal_metric_version",
        "comparisons",
    }:
        raise ValueError("successor safety evidence fields changed")
    if value.get("schema_version") != SAFETY_EVIDENCE_VERSION:
        raise ValueError("successor safety evidence version changed")
    if value.get("temporal_metric_version") != TEMPORAL_METRIC_VERSION:
        raise ValueError("successor temporal safety metric is incomplete")
    comparisons = value.get("comparisons")
    if not isinstance(comparisons, Mapping) or set(comparisons) != {
        "e1_minus_t1",
        "t1_minus_t0",
    }:
        raise ValueError("successor safety comparisons are incomplete")
    normalized: dict[str, Any] = {}
    for name, comparison in comparisons.items():
        if not isinstance(comparison, Mapping) or set(comparison) != {
            *CRITICAL_METRICS,
            "noninferior",
        }:
            raise ValueError(f"successor safety comparison is incomplete: {name}")
        row: dict[str, Any] = {}
        for metric in CRITICAL_METRICS:
            metric_row = comparison.get(metric)
            if not isinstance(metric_row, Mapping) or set(metric_row) != {
                "treatment",
                "reference",
                "delta",
            }:
                raise ValueError(f"successor safety metric is incomplete: {metric}")
            treatment = metric_row.get("treatment")
            reference = metric_row.get("reference")
            delta = metric_row.get("delta")
            if (
                type(treatment) is not int
                or treatment < 0
                or type(reference) is not int
                or reference < 0
                or type(delta) is not int
                or delta != treatment - reference
            ):
                raise ValueError(f"successor safety metric is invalid: {metric}")
            row[metric] = dict(metric_row)
        expected_noninferior = all(
            row[metric]["delta"] <= 0 for metric in CRITICAL_METRICS
        )
        if comparison.get("noninferior") is not expected_noninferior:
            raise ValueError(f"successor safety noninferiority changed: {name}")
        row["noninferior"] = expected_noninferior
        normalized[str(name)] = row
    return {
        "schema_version": SAFETY_EVIDENCE_VERSION,
        "temporal_metric_version": TEMPORAL_METRIC_VERSION,
        "comparisons": normalized,
    }


def promotion_assessment(
    *,
    primary: Mapping[str, Any],
    secondary: Mapping[str, Any],
    safety_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Require noninferiority and zero absolute critical treatment failures."""

    verified = validate_safety_evidence(safety_evidence)
    comparisons = verified["comparisons"]
    result = legacy.promotion_assessment(
        primary=primary,
        secondary=secondary,
        safety_comparisons=comparisons,
    )

    def strengthen(candidate: Mapping[str, Any], name: str) -> dict[str, Any]:
        strengthened = dict(candidate)
        gates = dict(strengthened["gates"])
        gates["zero_critical_safety_failures"] = all(
            comparisons[name][metric]["treatment"] == 0
            for metric in CRITICAL_METRICS
        )
        strengthened["gates"] = gates
        strengthened["promoted"] = all(gates.values())
        return strengthened

    e1 = strengthen(result["e1"], "e1_minus_t1")
    t1 = strengthen(result["t1_fallback"], "t1_minus_t0")
    t1["promoted"] = not e1["promoted"] and t1["promoted"]
    return {
        **result,
        "promoted": e1["promoted"] or t1["promoted"],
        "decision": (
            "promote_e1"
            if e1["promoted"]
            else "promote_t1"
            if t1["promoted"]
            else "do_not_promote"
        ),
        "e1": e1,
        "t1_fallback": t1,
        "safety_evidence": verified,
    }


def assemble_result(
    *,
    safety_evidence: Mapping[str, Any],
    **legacy_inputs: Any,
) -> dict[str, Any]:
    """Assemble through the historical statistics, then replace its safety gate."""

    verified_safety = validate_safety_evidence(safety_evidence)
    answer_behavior = legacy_inputs.get("answer_behavior_outcomes")
    if not isinstance(answer_behavior, Mapping):
        raise ValueError("successor assembly requires answer behavior outcomes")
    derived_safety = legacy.safety_comparisons(answer_behavior)
    for contrast in ("e1_minus_t1", "t1_minus_t0"):
        for metric in ("unsupported_answers", "citation_failures"):
            if (
                verified_safety["comparisons"][contrast][metric]
                != derived_safety[contrast][metric]
            ):
                raise ValueError(
                    f"successor safety evidence differs from behavior: {metric}"
                )
    result = legacy.assemble_result(**legacy_inputs)
    result["analysis_version"] = ANALYSIS_VERSION
    result["status"] = "completed_unsealed_successor_analysis"
    result["promotion_assessment"] = promotion_assessment(
        primary=result["contrasts"]["e1_minus_t1"],
        secondary=result["contrasts"]["t1_minus_t0"],
        safety_evidence=verified_safety,
    )
    legacy.canonical_json_bytes(result)
    return result
