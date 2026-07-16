#!/usr/bin/env python3
"""Pre-answer-sealed deterministic grading and analysis primitives for A11b.

This module intentionally makes no model calls.  The answer controller binds
``registered_analysis_config`` before launch.  After all arms finish, a caller
must prove exact completion coverage before the gold loader can be invoked.
Panel execution remains a separate, arm-blind phase.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import paired_stats


ANALYSIS_VERSION = "a11b-t0-t1-e1-analysis-v1"
COMPLETION_COVERAGE_VERSION = "a11b-completion-coverage-v1"
COMPLETION_KIND = "a11b_attempt_completion"
COMPLETION_SCHEMA_VERSION = "a11b-attempt-v1"
ARMS = ("t0", "t1", "e1")
EXPECTED_QUESTION_COUNT = 384
EXPECTED_COMPLETION_COUNT = EXPECTED_QUESTION_COUNT * len(ARMS)
EXPECTED_ANSWERABLE_COUNT = 288
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 20260716
CONTRAST_ALPHA = 0.025
PANEL_MODEL = "gpt-5.6-sol"
PANEL_EFFORT = "high"
PANEL_VOTES = 3
PANEL_BATCH_SIZE = 20
PANEL_TIMEOUT_SECONDS = 600
REGISTERED_DATASET_MANIFEST_SHA256 = (
    "c779c1e0c4e090a2f17c8856fef2999332f0ab1fd3106831dd945b0ef0863e0a"
)
REGISTERED_AUDIT_MANIFEST_SHA256 = (
    "166f0e69819aa020f5390801ab39540750b51eb88865c113e39c8fe751fc6f67"
)
REGISTERED_ANALYSIS_ORDER = (
    "hard_failures",
    "primary_e1_minus_t1_all_efficacy",
    "secondary_t1_minus_t0_all_efficacy",
    "mechanism_outcomes",
    "answer_behavior_outcomes",
    "economics",
    "family_depth_breakdowns",
    "temporal_policy_breakdowns",
    "difficulty_breakdowns",
    "answerability_breakdowns",
)
REGISTERED_FAMILIES = (
    "observation_finding",
    "observation_specimen",
    "diagnostic_finding",
    "diagnostic_specimen",
)
REGISTERED_CELLS = tuple(
    (family, depth) for family in REGISTERED_FAMILIES for depth in (2, 3)
)


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def registered_analysis_config(
    *,
    codex_bin: str,
    codex_version: str,
    codex_binary_sha256: str,
    answer_schema_sha256: str,
    panel_source_sha256: str,
    grading_source_sha256: str,
) -> dict[str, Any]:
    """Return every pre-answer analysis choice the controller must bind."""

    if not isinstance(codex_bin, str) or not codex_bin:
        raise ValueError("codex_bin must be a non-empty resolved path")
    codex_path = Path(codex_bin)
    if not codex_path.is_absolute() or codex_path.resolve() != codex_path:
        raise ValueError("codex_bin must be an absolute resolved path")
    if not isinstance(codex_version, str) or not codex_version:
        raise ValueError("codex_version must be non-empty")
    hashes = {
        "codex_binary_sha256": codex_binary_sha256,
        "answer_schema_sha256": answer_schema_sha256,
        "panel_source_sha256": panel_source_sha256,
        "grading_source_sha256": grading_source_sha256,
    }
    invalid = sorted(name for name, value in hashes.items() if not _is_sha256(value))
    if invalid:
        raise ValueError("analysis config has invalid sha256 fields: " + ",".join(invalid))
    return {
        "analysis_version": ANALYSIS_VERSION,
        "dataset_manifest_sha256": REGISTERED_DATASET_MANIFEST_SHA256,
        "arms": list(ARMS),
        "expected_question_count": EXPECTED_QUESTION_COUNT,
        "expected_completion_count": EXPECTED_COMPLETION_COUNT,
        "analysis_order": list(REGISTERED_ANALYSIS_ORDER),
        "contrasts": [
            {
                "name": "e1_minus_t1",
                "treatment": "e1",
                "reference": "t1",
                "stratum": "all_efficacy",
                "expected_n": EXPECTED_QUESTION_COUNT,
            },
            {
                "name": "t1_minus_t0",
                "treatment": "t1",
                "reference": "t0",
                "stratum": "all_efficacy",
                "expected_n": EXPECTED_QUESTION_COUNT,
            },
        ],
        "bootstrap": {
            "method": "patient_cluster_percentile",
            "replicates": BOOTSTRAP_REPLICATES,
            "seed": BOOTSTRAP_SEED,
            "contrast_alpha": CONTRAST_ALPHA,
            "confidence": 1 - CONTRAST_ALPHA,
        },
        "promotion": {
            "contrast": "e1_minus_t1",
            "positive_estimate_required": True,
            "cluster_ci_must_exclude_zero_in_positive_direction": True,
            "critical_safety_failures_allowed": 0,
            "mcnemar_is_report_only": True,
            "t1_fallback_is_registered": True,
        },
        "panel": {
            "model": PANEL_MODEL,
            "reasoning_effort": PANEL_EFFORT,
            "votes": PANEL_VOTES,
            "batch_size": PANEL_BATCH_SIZE,
            "timeout_seconds": PANEL_TIMEOUT_SECONDS,
            "codex_bin": str(codex_path),
            "codex_version": codex_version,
            "codex_binary_sha256": codex_binary_sha256,
            "panel_source_sha256": panel_source_sha256,
        },
        "answer_schema_sha256": answer_schema_sha256,
        "grading_source_sha256": grading_source_sha256,
    }


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def deterministic_partition(
    *,
    question: Mapping[str, Any],
    gold: Mapping[str, Any],
    answer: Mapping[str, Any],
) -> tuple[int | None, dict[str, Any] | None]:
    """Return one conservative deterministic verdict or one panel item.

    The structured insufficiency field is the only deterministic A11b grading
    signal.  All substantive categorical answers remain arm-blind panel work.
    """

    question_id = question.get("question_id")
    if not _nonempty_string(question_id) or gold.get("question_id") != question_id:
        raise ValueError("question and gold identity differ")
    question_text = question.get("question")
    if not _nonempty_string(question_text):
        raise ValueError("question text must be non-empty")
    answerable = gold.get("answerable")
    if type(answerable) is not bool:
        raise ValueError("gold.answerable must be boolean")
    answer_text = answer.get("answer")
    if not isinstance(answer_text, str):
        raise ValueError("answer.answer must be a string")
    insufficiency_reason = answer.get("insufficiency_reason")
    if insufficiency_reason is not None and not isinstance(insufficiency_reason, str):
        raise ValueError("answer.insufficiency_reason must be string or null")
    source_resource_ids = answer.get("source_resource_ids")
    if not isinstance(source_resource_ids, list) or any(
        not _nonempty_string(value) for value in source_resource_ids
    ):
        raise ValueError("answer.source_resource_ids must be a string list")
    abstained = _nonempty_string(insufficiency_reason)
    if abstained:
        if answer_text != "Insufficient evidence." or source_resource_ids:
            return 0, None
        return (0 if answerable else 1), None

    panel_gold: dict[str, list[str]] | None
    if answerable:
        reference = gold.get("reference_answer")
        if not isinstance(reference, dict) or set(reference) != {"code", "display"}:
            raise ValueError("answerable A11b gold must have code/display aliases")
        aliases = sorted(
            {
                str(reference["code"]).strip(),
                str(reference["display"]).strip(),
            }
        )
        if len(aliases) != 2 or any(not alias for alias in aliases):
            raise ValueError("answerable A11b aliases must be two non-empty values")
        panel_gold = {"acceptable_any": aliases}
    else:
        if gold.get("reference_answer") is not None:
            raise ValueError("unanswerable A11b gold must not expose a reference answer")
        # The registered panel rubric only judges categorical aliases. A
        # substantive answer to an unanswerable item is deterministically
        # incorrect, so it must never become an undefined panel task.
        return 0, None
    return None, {
        "question_id": question_id,
        "question": question_text,
        "gold": panel_gold,
        "answer": answer_text,
        "insufficiency_reason": insufficiency_reason,
    }


ReceiptValidator = Callable[[Mapping[str, Any]], bool | None]
GoldLoader = Callable[[], Sequence[Mapping[str, Any]]]


def prove_exact_completion_coverage(
    coverage: Mapping[str, Any],
    *,
    receipt_validator: ReceiptValidator | None = None,
) -> tuple[str, ...]:
    """Prove exact 384 x 3 completion coverage without reading gold.

    ``receipt_validator`` is the explicit boundary for the final controller's
    file/hash/event verification.  This module validates the frozen envelope
    and identity fields even when no controller implementation is importable.
    """

    if coverage.get("schema_version") != COMPLETION_COVERAGE_VERSION:
        raise ValueError("unsupported A11b completion coverage schema")
    if receipt_validator is None:
        raise ValueError("controller receipt artifact validator is required")
    controller_sha = coverage.get("controller_manifest_sha256")
    if not _is_sha256(controller_sha):
        raise ValueError("completion coverage has no controller sha256")
    question_ids_value = coverage.get("question_ids")
    if not isinstance(question_ids_value, list):
        raise ValueError("completion coverage question_ids must be a list")
    question_ids = tuple(question_ids_value)
    if (
        len(question_ids) != EXPECTED_QUESTION_COUNT
        or len(set(question_ids)) != EXPECTED_QUESTION_COUNT
        or any(not _nonempty_string(question_id) for question_id in question_ids)
    ):
        raise ValueError("completion coverage must bind exactly 384 unique questions")
    if coverage.get("arms") != list(ARMS):
        raise ValueError("completion coverage arms or order changed")
    receipts = coverage.get("receipts")
    if not isinstance(receipts, list) or len(receipts) != EXPECTED_COMPLETION_COUNT:
        raise ValueError("completion coverage must contain exactly 1152 receipts")

    expected = {(arm, question_id) for question_id in question_ids for arm in ARMS}
    observed: set[tuple[str, str]] = set()
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise ValueError("completion receipt must be an object")
        identity = (receipt.get("arm"), receipt.get("question_id"))
        if identity not in expected or identity in observed:
            raise ValueError("completion receipt coverage is extra or duplicated")
        observed.add(identity)
        if (
            receipt.get("kind") != COMPLETION_KIND
            or receipt.get("schema_version") != COMPLETION_SCHEMA_VERSION
            or receipt.get("controller_manifest_sha256") != controller_sha
            or receipt.get("status") != "answered"
            or not isinstance(receipt.get("attempt_number"), int)
            or isinstance(receipt.get("attempt_number"), bool)
            or receipt["attempt_number"] < 1
        ):
            raise ValueError("completion receipt identity/status is not accepted")
        for field in (
            "answer_sha256",
            "event_log_sha256",
            "prompt_sha256",
            "stderr_log_sha256",
            "model_input_sha256",
            "schema_sha256",
        ):
            if not _is_sha256(receipt.get(field)):
                raise ValueError(f"completion receipt has invalid {field}")
        if receipt_validator(receipt) is not True:
            raise ValueError("controller receipt artifact validation failed")
    if observed != expected:
        raise ValueError("completion receipt coverage is incomplete")
    return question_ids


def load_gold_after_completion(
    coverage: Mapping[str, Any],
    *,
    gold_loader: GoldLoader,
    receipt_validator: ReceiptValidator | None = None,
) -> dict[str, dict[str, Any]]:
    """Invoke ``gold_loader`` only after exact completion proof succeeds."""

    question_ids = prove_exact_completion_coverage(
        coverage,
        receipt_validator=receipt_validator,
    )
    rows = gold_loader()
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise ValueError("gold loader must return a sequence of rows")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("gold row must be an object")
        question_id = row.get("question_id")
        if question_id in by_id or question_id not in question_ids:
            raise ValueError("gold coverage is duplicate or unexpected")
        by_id[str(question_id)] = dict(row)
    if set(by_id) != set(question_ids):
        raise ValueError("gold coverage does not match completed questions")
    return {question_id: by_id[question_id] for question_id in question_ids}


def _validate_binary_label(value: object, *, location: str) -> int:
    if type(value) is not int or value not in (0, 1):
        raise ValueError(f"{location} must be the integer 0 or 1")
    return value


def final_labels(
    *,
    question_ids: Sequence[str],
    deterministic: Mapping[str, Mapping[str, int]],
    panel_queue: Sequence[Mapping[str, Any]],
    panel_verdicts: Mapping[str, int],
) -> dict[str, dict[str, int]]:
    """Combine exact, non-overlapping deterministic and panel labels."""

    ordered_ids = tuple(sorted(question_ids))
    if len(ordered_ids) != len(set(ordered_ids)):
        raise ValueError("final label question IDs are duplicated")
    if set(deterministic) != set(ARMS):
        raise ValueError("deterministic label arms are not exact")
    panel_hosts: dict[str, tuple[str, str]] = {}
    for item in panel_queue:
        arm = item.get("arm")
        question_id = item.get("question_id")
        if arm not in ARMS or question_id not in ordered_ids:
            raise ValueError("panel queue host is outside the registered schedule")
        host = f"{arm}|{question_id}"
        if host in panel_hosts:
            raise ValueError("panel queue contains a duplicate host")
        panel_hosts[host] = (str(arm), str(question_id))
    if set(panel_verdicts) != set(panel_hosts):
        raise ValueError("panel verdict coverage is not exact")

    labels: dict[str, dict[str, int]] = {}
    for arm in ARMS:
        arm_deterministic = deterministic[arm]
        arm_labels: dict[str, int] = {}
        for question_id in ordered_ids:
            host = f"{arm}|{question_id}"
            deterministic_present = question_id in arm_deterministic
            panel_present = host in panel_verdicts
            if deterministic_present == panel_present:
                raise ValueError(f"{host} must have exactly one label source")
            value = (
                arm_deterministic[question_id]
                if deterministic_present
                else panel_verdicts[host]
            )
            arm_labels[question_id] = _validate_binary_label(
                value,
                location=host,
            )
        labels[arm] = arm_labels
    return labels


def paired_contrast(
    *,
    name: str,
    treatment: str,
    reference: str,
    question_ids: Sequence[str],
    questions: Mapping[str, Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    """Compute one registered treatment-minus-reference paired contrast."""

    if treatment not in ARMS or reference not in ARMS or treatment == reference:
        raise ValueError("contrast arms are invalid")
    ordered_ids = tuple(sorted(question_ids))
    if not ordered_ids or len(ordered_ids) != len(set(ordered_ids)):
        raise ValueError("contrast question IDs must be non-empty and unique")
    pairs: list[tuple[str, int, int]] = []
    patient_clusters: set[str] = set()
    for question_id in ordered_ids:
        question = questions.get(question_id)
        if not isinstance(question, Mapping):
            raise ValueError(f"missing contrast question {question_id}")
        patient_id = question.get("patient_cluster_sha256")
        if not _nonempty_string(patient_id):
            raise ValueError(f"contrast question has no patient: {question_id}")
        if str(patient_id) in patient_clusters:
            raise ValueError("A11b efficacy requires one unique Patient per question")
        patient_clusters.add(str(patient_id))
        try:
            treatment_label = labels[treatment][question_id]
            reference_label = labels[reference][question_id]
        except KeyError as exc:
            raise ValueError(f"contrast label is missing: {question_id}") from exc
        pairs.append(
            (
                str(patient_id),
                _validate_binary_label(
                    treatment_label,
                    location=f"{treatment}|{question_id}",
                ),
                _validate_binary_label(
                    reference_label,
                    location=f"{reference}|{question_id}",
                ),
            )
        )
    paired = paired_stats.paired_summary(
        pairs,
        n_boot=BOOTSTRAP_REPLICATES,
        alpha=CONTRAST_ALPHA,
        seed=BOOTSTRAP_SEED,
    )
    discordant = paired["discordant_a_only"] + paired["discordant_b_only"]
    return {
        "name": name,
        "orientation": "treatment_minus_reference",
        "treatment": treatment,
        "reference": reference,
        "n": paired["n"],
        "treatment_accuracy": paired["acc_a"],
        "reference_accuracy": paired["acc_b"],
        "accuracy_difference": paired["diff"],
        "discordant_treatment_only": paired["discordant_a_only"],
        "discordant_reference_only": paired["discordant_b_only"],
        "mcnemar": {
            "estimable": discordant > 0,
            "discordant_pairs": discordant,
            "exact_two_sided_p": paired["mcnemar_p"] if discordant else None,
            "promotion_gate": False,
        },
        "patient_cluster_bootstrap": paired["cluster_bootstrap"],
    }


def promotion_assessment(
    *,
    primary: Mapping[str, Any],
    secondary: Mapping[str, Any],
    safety_comparisons: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the registered E1 gate and the registered T1 fallback gate."""

    def effect_gates(value: Mapping[str, Any], *, label: str) -> dict[str, bool]:
        difference = value.get("accuracy_difference")
        bootstrap = value.get("patient_cluster_bootstrap")
        if (
            not isinstance(difference, (int, float))
            or isinstance(difference, bool)
            or not math.isfinite(float(difference))
            or not isinstance(bootstrap, Mapping)
        ):
            raise ValueError(f"{label} contrast is incomplete")
        ci_low = bootstrap.get("ci_low")
        if (
            not isinstance(ci_low, (int, float))
            or isinstance(ci_low, bool)
            or not math.isfinite(float(ci_low))
        ):
            raise ValueError(f"{label} cluster interval is incomplete")
        return {
            "positive_estimate": float(difference) > 0,
            "cluster_ci_excludes_zero_positive": float(ci_low) > 0,
        }

    if set(safety_comparisons) != {"e1_minus_t1", "t1_minus_t0"}:
        raise ValueError("registered safety comparisons are incomplete")
    for name, comparison in safety_comparisons.items():
        if not isinstance(comparison, Mapping) or type(
            comparison.get("noninferior")
        ) is not bool:
            raise ValueError(f"registered safety comparison is incomplete: {name}")
    e1_gates = {
        **effect_gates(primary, label="primary"),
        "safety_noninferior": safety_comparisons["e1_minus_t1"]["noninferior"],
    }
    t1_gates = {
        **effect_gates(secondary, label="secondary"),
        "safety_noninferior": safety_comparisons["t1_minus_t0"]["noninferior"],
    }
    e1_promoted = all(e1_gates.values())
    t1_promoted = not e1_promoted and all(t1_gates.values())
    return {
        "promoted": e1_promoted or t1_promoted,
        "decision": (
            "promote_e1"
            if e1_promoted
            else "promote_t1"
            if t1_promoted
            else "do_not_promote"
        ),
        "e1": {"promoted": e1_promoted, "gates": e1_gates},
        "t1_fallback": {"promoted": t1_promoted, "gates": t1_gates},
        "safety_comparisons": dict(safety_comparisons),
        "mcnemar_is_report_only": True,
    }


def safety_comparisons(
    answer_behavior_outcomes: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive every preregistered non-increase safety gate from arm metrics."""

    metrics = (
        "unsupported_answers",
        "citation_failures",
        "temporal_binding_errors",
    )
    if set(answer_behavior_outcomes) != set(ARMS):
        raise ValueError("registered answer behavior arms are incomplete")
    normalized: dict[str, dict[str, int]] = {}
    for arm in ARMS:
        row = answer_behavior_outcomes.get(arm)
        if not isinstance(row, Mapping):
            raise ValueError(f"registered answer behavior is incomplete: {arm}")
        normalized[arm] = {}
        for metric in metrics:
            value = row.get(metric)
            if type(value) is not int or value < 0:
                raise ValueError(f"registered safety metric is incomplete: {arm}.{metric}")
            normalized[arm][metric] = value

    def compare(treatment: str, reference: str) -> dict[str, Any]:
        result = {
            metric: {
                "treatment": normalized[treatment][metric],
                "reference": normalized[reference][metric],
                "delta": normalized[treatment][metric]
                - normalized[reference][metric],
            }
            for metric in metrics
        }
        result["noninferior"] = all(row["delta"] <= 0 for row in result.values())
        return result

    return {
        "e1_minus_t1": compare("e1", "t1"),
        "t1_minus_t0": compare("t1", "t0"),
    }


def _accuracy(
    labels: Mapping[str, Mapping[str, int]],
    question_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        correct = sum(
            _validate_binary_label(labels[arm][question_id], location=f"{arm}|{question_id}")
            for question_id in question_ids
        )
        result[arm] = {
            "n": len(question_ids),
            "correct": correct,
            "accuracy": correct / len(question_ids),
        }
    return result


def _family_depth_breakdowns(
    *,
    question_ids: Sequence[str],
    questions: Mapping[str, Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, Any]]:
    cells: dict[tuple[str, int], list[str]] = {cell: [] for cell in REGISTERED_CELLS}
    for question_id in question_ids:
        question = questions[question_id]
        cell = (question.get("family"), question.get("depth"))
        if cell not in cells:
            raise ValueError(f"question has an unregistered family/depth: {question_id}")
        cells[cell].append(question_id)
    if any(len(ids) != 48 for ids in cells.values()):
        raise ValueError("A11b family/depth cells must contain exactly 48 questions")
    return {
        f"{family}:depth-{depth}": {
            "family": family,
            "depth": depth,
            "n": len(cells[(family, depth)]),
            "accuracy_by_arm": _accuracy(labels, sorted(cells[(family, depth)])),
        }
        for family, depth in REGISTERED_CELLS
    }


def _categorical_breakdown(
    *,
    question_ids: Sequence[str],
    questions: Mapping[str, Mapping[str, Any]],
    gold: Mapping[str, Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, int]],
    field: str,
    source: str,
) -> dict[str, dict[str, Any]]:
    """Report a deterministic accuracy breakdown without affecting promotion."""

    if source not in {"question", "gold"}:
        raise ValueError("breakdown source changed")
    cells: dict[str, list[str]] = {}
    for question_id in question_ids:
        row = questions[question_id] if source == "question" else gold[question_id]
        value = row.get(field)
        if isinstance(value, bool):
            key = "answerable" if value else "unanswerable"
        elif isinstance(value, (str, int)) and not isinstance(value, bool):
            key = str(value)
        else:
            raise ValueError(f"A11b {field} breakdown value is invalid: {question_id}")
        cells.setdefault(key, []).append(question_id)
    if sum(len(ids) for ids in cells.values()) != len(question_ids):
        raise ValueError(f"A11b {field} breakdown coverage changed")
    return {
        key: {
            "value": key,
            "n": len(cells[key]),
            "accuracy_by_arm": _accuracy(labels, sorted(cells[key])),
            "promotion_gate": False,
        }
        for key in sorted(cells)
    }


def assemble_result(
    *,
    question_ids: Sequence[str],
    questions: Mapping[str, Mapping[str, Any]],
    gold: Mapping[str, Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, int]],
    mechanism_outcomes: Mapping[str, Any],
    answer_behavior_outcomes: Mapping[str, Any],
    economics: Mapping[str, Any],
    input_hashes: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble the fixed A11b result without consulting input insertion order."""

    ordered_ids = tuple(sorted(question_ids))
    if (
        len(ordered_ids) != EXPECTED_QUESTION_COUNT
        or len(set(ordered_ids)) != EXPECTED_QUESTION_COUNT
        or set(questions) != set(ordered_ids)
        or set(gold) != set(ordered_ids)
        or set(labels) != set(ARMS)
        or any(set(labels[arm]) != set(ordered_ids) for arm in ARMS)
    ):
        raise ValueError("A11b final result requires exact 384 x 3 coverage")
    if any(
        questions[question_id].get("question_id") != question_id
        or gold[question_id].get("question_id") != question_id
        for question_id in ordered_ids
    ):
        raise ValueError("A11b question/gold rows are misbound to their keys")
    answerable_ids = tuple(
        question_id
        for question_id in ordered_ids
        if gold[question_id].get("answerable") is True
    )
    if len(answerable_ids) != EXPECTED_ANSWERABLE_COUNT:
        raise ValueError("A11b final result requires exactly 288 answerable questions")
    if any(type(gold[question_id].get("answerable")) is not bool for question_id in ordered_ids):
        raise ValueError("every A11b gold row must have boolean answerable")

    primary = paired_contrast(
        name="e1_minus_t1",
        treatment="e1",
        reference="t1",
        question_ids=ordered_ids,
        questions=questions,
        labels=labels,
    )
    secondary = paired_contrast(
        name="t1_minus_t0",
        treatment="t1",
        reference="t0",
        question_ids=ordered_ids,
        questions=questions,
        labels=labels,
    )
    if not mechanism_outcomes or not economics or not input_hashes:
        raise ValueError("A11b final result requires mechanism, economics, and input receipts")
    safety = safety_comparisons(answer_behavior_outcomes)
    promotion = promotion_assessment(
        primary=primary,
        secondary=secondary,
        safety_comparisons=safety,
    )
    result = {
        "analysis_version": ANALYSIS_VERSION,
        "status": "completed_registered_analysis",
        "analysis_order": list(REGISTERED_ANALYSIS_ORDER),
        "question_ids": list(ordered_ids),
        "arms": list(ARMS),
        "registered_contrasts": ["e1_minus_t1", "t1_minus_t0"],
        "accuracy_by_arm": _accuracy(labels, ordered_ids),
        "contrasts": {
            "e1_minus_t1": primary,
            "t1_minus_t0": secondary,
        },
        "promotion_assessment": promotion,
        "family_depth_breakdowns": _family_depth_breakdowns(
            question_ids=ordered_ids,
            questions=questions,
            labels=labels,
        ),
        "temporal_policy_breakdowns": _categorical_breakdown(
            question_ids=ordered_ids,
            questions=questions,
            gold=gold,
            labels=labels,
            field="temporal_policy",
            source="question",
        ),
        "difficulty_breakdowns": _categorical_breakdown(
            question_ids=ordered_ids,
            questions=questions,
            gold=gold,
            labels=labels,
            field="difficulty",
            source="question",
        ),
        "answerability_breakdowns": _categorical_breakdown(
            question_ids=ordered_ids,
            questions=questions,
            gold=gold,
            labels=labels,
            field="answerable",
            source="gold",
        ),
        "mechanism_outcomes": dict(mechanism_outcomes),
        "answer_behavior_outcomes": dict(answer_behavior_outcomes),
        "economics": dict(economics),
        "input_hashes": dict(input_hashes),
    }
    # Enforce JSON determinism and reject non-finite derived values now.
    canonical_json_bytes(result)
    return result


def write_result(path: Path, result: Mapping[str, Any]) -> None:
    """Atomically write the canonical, byte-deterministic final result."""

    payload = canonical_json_bytes(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)
