#!/usr/bin/env python3
"""Deterministic, panel-free grading for the A11b successor development gate."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from typing import Any

import a11b_answer_contract
from a11_evidence_core import canonical_bytes, sha256


GRADING_VERSION = "a11b-successor-development-exact-alias-grading-v2"
RESULT_MANIFEST_VERSION = "a11b-successor-development-result-manifest-v3"
ARMS = ("t0", "t1", "e1")
QUESTION_COUNT = 64
TOKEN_FIELDS = ("input", "cached", "output", "reasoning", "total")


def _normalized_alias(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def is_correct(*, gold: Mapping[str, Any], answer: Mapping[str, Any]) -> bool:
    """Apply the frozen conservative development correctness endpoint."""

    validated = a11b_answer_contract.validate_answer(answer)
    answerable = gold.get("answerable")
    if type(answerable) is not bool:
        raise ValueError("development gold answerability is invalid")
    if not answerable:
        if gold.get("reference_answer") is not None:
            raise ValueError("unanswerable development gold has a reference answer")
        return validated["status"] == a11b_answer_contract.INSUFFICIENT
    reference = gold.get("reference_answer")
    if not isinstance(reference, Mapping) or set(reference) != {"code", "display"}:
        raise ValueError("answerable development gold aliases are invalid")
    aliases = {reference["code"], reference["display"]}
    if any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
        raise ValueError("answerable development gold aliases are invalid")
    if validated["status"] != a11b_answer_contract.ANSWERED:
        return False
    return _normalized_alias(validated["answer"]) in {
        _normalized_alias(alias) for alias in aliases
    }


def _validated_complete_usage(value: object, *, expected_source: str) -> dict[str, Any]:
    expected_fields = {*TOKEN_FIELDS, "complete", "source"}
    if (
        not isinstance(value, Mapping)
        or set(value) != expected_fields
        or value.get("complete") is not True
        or value.get("source") != expected_source
        or any(type(value.get(field)) is not int for field in TOKEN_FIELDS)
        or any(int(value[field]) < 0 for field in TOKEN_FIELDS)
        or value["cached"] > value["input"]
        or value["reasoning"] > value["output"]
        or value["total"] != value["input"] + value["output"]
    ):
        raise ValueError("development token usage is invalid")
    return dict(value)


def _token_totals(rows: list[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    totals = {arm: {field: 0 for field in TOKEN_FIELDS} for arm in ARMS}
    for row in rows:
        arm = str(row["arm"])
        usage = row["token_usage"]
        assert isinstance(usage, Mapping)
        for field in TOKEN_FIELDS:
            totals[arm][field] += int(usage[field])
    return totals


def compile_result(
    *,
    gold_rows: list[Mapping[str, Any]],
    accepted_answers: list[Mapping[str, Any]],
    all_attempts: list[Mapping[str, Any]],
    audit_manifest_sha256: str,
) -> dict[str, Any]:
    """Compile content-free gate inputs after exact development completion."""

    if (
        len(gold_rows) != QUESTION_COUNT
        or not isinstance(audit_manifest_sha256, str)
        or len(audit_manifest_sha256) != 64
        or any(
            character not in "0123456789abcdef" for character in audit_manifest_sha256
        )
    ):
        raise ValueError("development grading requires exactly 64 gold rows")
    gold: dict[str, Mapping[str, Any]] = {}
    clusters: set[str] = set()
    assignments: list[dict[str, str]] = []
    for row in gold_rows:
        question_id = row.get("question_id")
        cluster = row.get("patient_cluster_sha256")
        if (
            not isinstance(question_id, str)
            or not question_id
            or question_id in gold
            or not isinstance(cluster, str)
            or len(cluster) != 64
            or any(character not in "0123456789abcdef" for character in cluster)
            or cluster in clusters
        ):
            raise ValueError("development gold identities are not patient-disjoint")
        gold[question_id] = row
        clusters.add(cluster)
        assignments.append(
            {"question_id": question_id, "patient_cluster_sha256": cluster}
        )

    expected = {(question_id, arm) for question_id in gold for arm in ARMS}
    if len(accepted_answers) != len(expected):
        raise ValueError("development grading requires exactly 192 accepted answers")
    accepted: dict[tuple[str, str], Mapping[str, Any]] = {}
    outcomes: list[dict[str, Any]] = []
    for row in accepted_answers:
        if set(row) != {
            "question_id",
            "arm",
            "answer",
            "answer_adaptation",
            "token_usage",
        }:
            raise ValueError("accepted development answer fields changed")
        identity = (row.get("question_id"), row.get("arm"))
        if identity not in expected or identity in accepted:
            raise ValueError("accepted development answer coverage changed")
        usage = _validated_complete_usage(
            row.get("token_usage"), expected_source="turn.completed"
        )
        answer = row.get("answer")
        if not isinstance(answer, Mapping):
            raise ValueError("accepted development answer is invalid")
        validated = a11b_answer_contract.validate_answer(answer)
        adaptation = row.get("answer_adaptation")
        if not isinstance(adaptation, Mapping):
            raise ValueError("accepted development answer adaptation is invalid")
        validated_adaptation = a11b_answer_contract.validate_adaptation_record(
            adaptation
        )
        if validated_adaptation["canonical_answer"] != validated:
            raise ValueError("accepted answer differs from its canonical adaptation")
        question_id, arm = identity
        assert isinstance(question_id, str) and isinstance(arm, str)
        cluster = str(gold[question_id]["patient_cluster_sha256"])
        accepted[identity] = {
            **row,
            "answer_adaptation": validated_adaptation,
            "token_usage": usage,
        }
        outcomes.append(
            {
                "question_id": question_id,
                "patient_cluster_sha256": cluster,
                "arm": arm,
                "correct": is_correct(gold=gold[question_id], answer=validated),
                "answer_status": str(validated["status"]),
            }
        )
    if set(accepted) != expected:
        raise ValueError("accepted development answer coverage changed")

    if not len(expected) <= len(all_attempts) <= len(expected) * 3:
        raise ValueError("development attempt count is outside the registered bound")
    attempts: dict[tuple[str, str], list[Mapping[str, Any]]] = {
        identity: [] for identity in expected
    }
    for row in all_attempts:
        if set(row) != {
            "question_id",
            "arm",
            "attempt_number",
            "outcome",
            "token_usage",
        }:
            raise ValueError("development attempt fields changed")
        identity = (row.get("question_id"), row.get("arm"))
        attempt_number = row.get("attempt_number")
        if (
            identity not in expected
            or type(attempt_number) is not int
            or not 1 <= attempt_number <= 3
            or row.get("outcome") not in {"accepted", "provider_failure"}
        ):
            raise ValueError("development attempt receipt is invalid")
        expected_source = (
            "turn.completed" if row["outcome"] == "accepted" else "provider.error"
        )
        usage = _validated_complete_usage(
            row.get("token_usage"), expected_source=expected_source
        )
        attempts[identity].append({**row, "token_usage": usage})
    for identity, rows in attempts.items():
        ordered = sorted(rows, key=lambda row: int(row["attempt_number"]))
        if (
            [row["attempt_number"] for row in ordered]
            != list(range(1, len(ordered) + 1))
            or ordered[-1]["outcome"] != "accepted"
            or any(row["outcome"] == "accepted" for row in ordered[:-1])
            or ordered[-1]["token_usage"] != accepted[identity]["token_usage"]
        ):
            raise ValueError("development attempt sequence is invalid")

    accepted_token_receipts = [
        {
            "question_id": question_id,
            "arm": arm,
            "token_usage": accepted[(question_id, arm)]["token_usage"],
        }
        for question_id, arm in sorted(expected)
    ]
    answer_adaptations = [
        {
            "question_id": question_id,
            "arm": arm,
            **accepted[(question_id, arm)]["answer_adaptation"],
        }
        for question_id, arm in sorted(expected)
    ]
    all_attempt_receipts = [
        {
            "question_id": str(row["question_id"]),
            "arm": str(row["arm"]),
            "attempt_number": int(row["attempt_number"]),
            "outcome": str(row["outcome"]),
            "token_usage": row["token_usage"],
        }
        for identity in sorted(attempts)
        for row in sorted(
            attempts[identity], key=lambda item: int(item["attempt_number"])
        )
    ]
    provider_failures = {
        arm: sum(
            row["arm"] == arm and row["outcome"] == "provider_failure"
            for row in all_attempt_receipts
        )
        for arm in ARMS
    }
    accepted_after_retry = {
        arm: sum(
            identity[1] == arm and len(rows) > 1 for identity, rows in attempts.items()
        )
        for arm in ARMS
    }
    manifest = {
        "schema_version": RESULT_MANIFEST_VERSION,
        "audit_manifest_sha256": audit_manifest_sha256,
        "gold_rows_sha256": sha256(canonical_bytes(gold_rows)),
        "assignments_sha256": sha256(canonical_bytes(assignments)),
        "outcomes_sha256": sha256(canonical_bytes(outcomes)),
        "answer_adaptations_sha256": sha256(canonical_bytes(answer_adaptations)),
        "accepted_token_receipts_sha256": sha256(
            canonical_bytes(accepted_token_receipts)
        ),
        "all_attempt_token_receipts_sha256": sha256(
            canonical_bytes(all_attempt_receipts)
        ),
        "question_count": QUESTION_COUNT,
        "arms": list(ARMS),
        "accepted_attempts": len(accepted_answers),
        "all_attempts": len(all_attempts),
        "accepted_token_usage_complete": True,
        "all_attempt_token_usage_complete": True,
        "token_economics": {
            "accepted_by_arm": _token_totals(accepted_token_receipts),
            "all_attempts_by_arm": _token_totals(all_attempt_receipts),
            "provider_failures_by_arm": provider_failures,
            "accepted_after_retry_by_arm": accepted_after_retry,
        },
    }
    return {
        "schema_version": GRADING_VERSION,
        "assignments": assignments,
        "outcomes": outcomes,
        "answer_adaptations": answer_adaptations,
        "manifest": manifest,
        "model_calls": 0,
    }
