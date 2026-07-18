#!/usr/bin/env python3
"""Fail-closed development-discordance gate for the A11b successor."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from a11_evidence_core import canonical_bytes, sha256


GATE_VERSION = "a11b-successor-development-discordance-gate-v1"
ASSIGNMENT_FIELDS = frozenset({"question_id", "patient_cluster_sha256"})
OUTCOME_FIELDS = ASSIGNMENT_FIELDS | frozenset(
    {"arm", "correct", "answer_status"}
)
ARMS = ("t0", "t1", "e1")
CONTRASTS = (
    ("primary_e1_minus_t1", "e1", "t1"),
    ("secondary_t1_minus_t0", "t1", "t0"),
)
QUESTION_COUNT = 64
MINIMUM_DISCORDANT_PAIRS = 1
RESULT_MANIFEST_VERSION = "a11b-successor-development-result-manifest-v1"
RESULT_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "assignments_sha256",
        "outcomes_sha256",
        "question_count",
        "arms",
        "accepted_attempts",
        "all_attempts",
        "accepted_token_usage_complete",
        "all_attempt_token_usage_complete",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_GATE_INPUT_BYTES = 2 * 1024 * 1024


def _assignment_index(
    assignments: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    values = list(assignments)
    if len(values) != QUESTION_COUNT:
        raise ValueError("development gate requires exactly 64 assignments")
    result: dict[str, str] = {}
    clusters: set[str] = set()
    for value in values:
        if not isinstance(value, Mapping) or set(value) != ASSIGNMENT_FIELDS:
            raise ValueError("development assignment fields changed")
        question_id = value.get("question_id")
        cluster = value.get("patient_cluster_sha256")
        if not isinstance(question_id, str) or not question_id.strip():
            raise ValueError("development question identity is invalid")
        if not isinstance(cluster, str) or _SHA256.fullmatch(cluster) is None:
            raise ValueError("development patient cluster is invalid")
        if question_id in result or cluster in clusters:
            raise ValueError("development assignments are not patient-disjoint")
        result[question_id] = cluster
        clusters.add(cluster)
    return result


def _outcome_index(
    outcomes: Iterable[Mapping[str, Any]],
    *,
    assignments: dict[str, str],
) -> dict[str, dict[str, dict[str, Any]]]:
    values = list(outcomes)
    if len(values) != QUESTION_COUNT * len(ARMS):
        raise ValueError("development gate requires exactly 192 outcomes")
    result: dict[str, dict[str, dict[str, Any]]] = {
        question_id: {} for question_id in assignments
    }
    for value in values:
        if not isinstance(value, Mapping) or set(value) != OUTCOME_FIELDS:
            raise ValueError("development outcome fields changed")
        question_id = value.get("question_id")
        cluster = value.get("patient_cluster_sha256")
        arm = value.get("arm")
        correct = value.get("correct")
        status = value.get("answer_status")
        if question_id not in assignments or assignments[question_id] != cluster:
            raise ValueError("development outcome patient binding changed")
        if arm not in ARMS or arm in result[question_id]:
            raise ValueError("development outcome arm coverage changed")
        if type(correct) is not bool:
            raise ValueError("development correctness must be boolean")
        if status not in {"answered", "insufficient"}:
            raise ValueError("development answer status is invalid")
        result[question_id][arm] = dict(value)
    if any(set(by_arm) != set(ARMS) for by_arm in result.values()):
        raise ValueError("development outcome arm coverage changed")
    return result


def _contrast_counts(
    outcomes: dict[str, dict[str, dict[str, Any]]],
    *,
    treatment: str,
    reference: str,
) -> dict[str, int]:
    counts = {
        "treatment_only_correct": 0,
        "reference_only_correct": 0,
        "both_correct": 0,
        "both_incorrect": 0,
        "answer_status_discordant": 0,
    }
    for by_arm in outcomes.values():
        treatment_value = by_arm[treatment]
        reference_value = by_arm[reference]
        pair = (treatment_value["correct"], reference_value["correct"])
        if pair == (True, False):
            counts["treatment_only_correct"] += 1
        elif pair == (False, True):
            counts["reference_only_correct"] += 1
        elif pair == (True, True):
            counts["both_correct"] += 1
        else:
            counts["both_incorrect"] += 1
        if treatment_value["answer_status"] != reference_value["answer_status"]:
            counts["answer_status_discordant"] += 1
    counts["discordant"] = (
        counts["treatment_only_correct"] + counts["reference_only_correct"]
    )
    return counts


def compile_gate_receipt(
    *,
    assignments: Iterable[Mapping[str, Any]],
    outcomes: Iterable[Mapping[str, Any]],
    development_result_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile a content-free receipt only when both contrasts have headroom."""

    assignment_values = list(assignments)
    outcome_values = list(outcomes)
    if (
        not isinstance(development_result_manifest, Mapping)
        or set(development_result_manifest) != RESULT_MANIFEST_FIELDS
        or development_result_manifest.get("schema_version")
        != RESULT_MANIFEST_VERSION
        or development_result_manifest.get("assignments_sha256")
        != sha256(canonical_bytes(assignment_values))
        or development_result_manifest.get("outcomes_sha256")
        != sha256(canonical_bytes(outcome_values))
        or development_result_manifest.get("question_count") != QUESTION_COUNT
        or development_result_manifest.get("arms") != list(ARMS)
        or development_result_manifest.get("accepted_attempts")
        != QUESTION_COUNT * len(ARMS)
        or type(development_result_manifest.get("all_attempts")) is not int
        or not QUESTION_COUNT * len(ARMS)
        <= development_result_manifest["all_attempts"]
        <= QUESTION_COUNT * len(ARMS) * 3
        or development_result_manifest.get("accepted_token_usage_complete")
        is not True
        or development_result_manifest.get("all_attempt_token_usage_complete")
        is not True
    ):
        raise ValueError("development result manifest binding is invalid")
    assignment_index = _assignment_index(assignment_values)
    outcome_index = _outcome_index(
        outcome_values,
        assignments=assignment_index,
    )
    contrast_receipts: dict[str, dict[str, Any]] = {}
    for name, treatment, reference in CONTRASTS:
        counts = _contrast_counts(
            outcome_index,
            treatment=treatment,
            reference=reference,
        )
        if counts["discordant"] < MINIMUM_DISCORDANT_PAIRS:
            raise ValueError(f"{name} has no discordant correctness pairs")
        contrast_receipts[name] = {
            "treatment_arm": treatment,
            "reference_arm": reference,
            **counts,
        }
    return {
        "schema_version": GATE_VERSION,
        "status": "passed",
        "development_result_manifest_sha256": (
            sha256(canonical_bytes(development_result_manifest))
        ),
        "question_count": QUESTION_COUNT,
        "answer_call_count": QUESTION_COUNT * len(ARMS),
        "accepted_attempts": development_result_manifest[
            "accepted_attempts"
        ],
        "all_attempts": development_result_manifest["all_attempts"],
        "token_usage_complete": True,
        "patient_disjoint": True,
        "minimum_discordant_pairs_per_contrast": MINIMUM_DISCORDANT_PAIRS,
        "contrasts": contrast_receipts,
        "model_calls": 0,
    }


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = child
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _load_value(path: Path, *, label: str) -> Any:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError(f"invalid {label} JSON") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > MAX_GATE_INPUT_BYTES
        ):
            raise ValueError(f"invalid {label} file")
        payload = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            payload.extend(chunk)
            if len(payload) > MAX_GATE_INPUT_BYTES:
                raise ValueError(f"{label} exceeds byte bound")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or len(payload) != before.st_size:
            raise ValueError(f"{label} changed during read")
    finally:
        os.close(descriptor)
    try:
        return json.loads(
            bytes(payload),
            object_pairs_hook=_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"invalid {label} JSON") from exc


def _load_array(path: Path, *, label: str) -> list[dict[str, Any]]:
    value = _load_value(path, label=label)
    if not isinstance(value, list) or any(
        not isinstance(item, dict) for item in value
    ):
        raise ValueError(f"{label} must be a JSON array of objects")
    return value


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    value = _load_value(path, label=label)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assignments", type=Path, required=True)
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--result-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = compile_gate_receipt(
        assignments=_load_array(args.assignments, label="assignment"),
        outcomes=_load_array(args.outcomes, label="outcome"),
        development_result_manifest=_load_object(
            args.result_manifest,
            label="result manifest",
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as handle:
        handle.write(canonical_bytes(receipt) + b"\n")


if __name__ == "__main__":
    main()
