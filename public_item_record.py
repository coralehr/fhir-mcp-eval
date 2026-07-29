#!/usr/bin/env python3
"""Validate privacy-minimized, item-level public evaluation records.

This contract is intentionally stricter than the private run manifests. Public
records carry opaque identifiers, hashes, scores, receipts, and economics. They
do not carry raw clinical text, questions, answers, prompts, traces, or model
reasoning. A hash binds each minimized record to the restricted source without
publishing that source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "public-eval-item-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIERS = {
    "record_id": re.compile(r"^pubitem_[0-9a-f]{24}$"),
    "question_id": re.compile(r"^pubq_[0-9a-f]{24}$"),
    "cluster_id": re.compile(r"^pubc_[0-9a-f]{24}$"),
    "arm_id": re.compile(r"^pubarm_[0-9a-f]{16}$"),
    "grading_id": re.compile(r"^grade_[0-9a-f]{24}$"),
}
RESOURCE_ID_RE = re.compile(r"^pubres_[0-9a-f]{24}$")
EXPERIMENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")

TOP_LEVEL_FIELDS = {
    "schema_version",
    "record_id",
    "experiment_id",
    "question_id",
    "cluster_id",
    "arm_id",
    "grading_id",
    "evidence",
    "execution",
    "outcome",
    "grading",
    "economics",
    "privacy",
}
SECTION_FIELDS = {
    "evidence": {
        "selected_resource_ids",
        "packet_sha256",
        "restricted_source_sha256",
    },
    "execution": {
        "provider",
        "model",
        "server",
        "prompt_sha256",
        "schema_sha256",
        "reasoning_effort",
        "seed",
        "retry_policy",
    },
    "outcome": {"representation", "value", "answer_sha256"},
    "grading": {
        "deterministic_grade",
        "panel_votes",
        "human_adjudication",
        "exclusion_reason",
    },
    "economics": {
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "total_tokens",
        "provider_cost_usd",
        "latency_ms",
        "retry_count",
        "transport_failure_count",
    },
    "privacy": {
        "contains_raw_clinical_text",
        "omitted_fields",
        "omission_rationale",
        "review_status",
    },
}


def _object(value: Any, *, location: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise ValueError(f"{location} has unknown field(s): {', '.join(unknown)}")
    if missing:
        raise ValueError(f"{location} missing field(s): {', '.join(missing)}")
    return value


def _string(value: Any, *, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must be a non-empty string")
    return value


def _sha256(value: Any, *, location: str) -> str:
    text = _string(value, location=location)
    if not SHA256_RE.fullmatch(text):
        raise ValueError(f"{location} must be a lowercase SHA-256 digest")
    return text


def _nonnegative_int(value: Any, *, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{location} must be a non-negative integer")
    return value


def _optional_binary(value: Any, *, location: str) -> None:
    if value is not None and (isinstance(value, bool) or value not in (0, 1)):
        raise ValueError(f"{location} must be null, 0, or 1")


def validate_record(record: Any) -> None:
    """Raise ValueError unless *record* exactly satisfies the public contract."""

    record = _object(record, location="record", fields=TOP_LEVEL_FIELDS)
    if record["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"schema_version must equal {SCHEMA_VERSION}")

    for field, pattern in IDENTIFIERS.items():
        value = _string(record[field], location=field)
        if not pattern.fullmatch(value):
            raise ValueError(f"{field} is not a valid opaque public identifier")
    experiment_id = _string(record["experiment_id"], location="experiment_id")
    if not EXPERIMENT_ID_RE.fullmatch(experiment_id):
        raise ValueError("experiment_id must be a stable lowercase slug")

    sections = {
        name: _object(record[name], location=name, fields=fields)
        for name, fields in SECTION_FIELDS.items()
    }

    evidence = sections["evidence"]
    resource_ids = evidence["selected_resource_ids"]
    if not isinstance(resource_ids, list) or any(
        not isinstance(value, str) or not RESOURCE_ID_RE.fullmatch(value)
        for value in resource_ids
    ):
        raise ValueError(
            "evidence.selected_resource_ids must contain only opaque public IDs"
        )
    if len(resource_ids) != len(set(resource_ids)):
        raise ValueError("evidence.selected_resource_ids contains duplicates")
    _sha256(evidence["packet_sha256"], location="evidence.packet_sha256")
    _sha256(
        evidence["restricted_source_sha256"],
        location="evidence.restricted_source_sha256",
    )

    execution = sections["execution"]
    for field in ("provider", "model", "server", "reasoning_effort", "retry_policy"):
        _string(execution[field], location=f"execution.{field}")
    _sha256(execution["prompt_sha256"], location="execution.prompt_sha256")
    _sha256(execution["schema_sha256"], location="execution.schema_sha256")
    if execution["seed"] is not None:
        _nonnegative_int(execution["seed"], location="execution.seed")

    outcome = sections["outcome"]
    representation = outcome["representation"]
    if representation != "categorical_score":
        raise ValueError("outcome.representation must equal categorical_score in v1")
    if outcome["value"] not in {"correct", "incorrect", "abstain", "excluded"}:
        raise ValueError("outcome.value is invalid for categorical_score")
    _sha256(outcome["answer_sha256"], location="outcome.answer_sha256")

    grading = sections["grading"]
    _optional_binary(
        grading["deterministic_grade"], location="grading.deterministic_grade"
    )
    if not isinstance(grading["panel_votes"], list):
        raise ValueError("grading.panel_votes must be an array")
    for vote in grading["panel_votes"]:
        if isinstance(vote, bool) or vote not in (0, 1):
            raise ValueError("grading.panel_votes must contain only 0 or 1")
    _optional_binary(
        grading["human_adjudication"], location="grading.human_adjudication"
    )
    if grading["exclusion_reason"] is not None:
        _string(grading["exclusion_reason"], location="grading.exclusion_reason")
    if outcome["value"] == "excluded" and grading["exclusion_reason"] is None:
        raise ValueError("grading.exclusion_reason is required for an excluded outcome")
    if outcome["value"] != "excluded" and grading["exclusion_reason"] is not None:
        raise ValueError(
            "grading.exclusion_reason is allowed only for an excluded outcome"
        )

    economics = sections["economics"]
    for field in (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "total_tokens",
        "latency_ms",
        "retry_count",
        "transport_failure_count",
    ):
        _nonnegative_int(economics[field], location=f"economics.{field}")
    expected_total = (
        economics["input_tokens"]
        + economics["cached_input_tokens"]
        + economics["output_tokens"]
    )
    if economics["total_tokens"] != expected_total:
        raise ValueError(
            "economics.total_tokens must equal input_tokens + cached_input_tokens + output_tokens"
        )
    cost = economics["provider_cost_usd"]
    if cost is not None and (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(cost)
        or cost < 0
    ):
        raise ValueError(
            "economics.provider_cost_usd must be null or a finite non-negative number"
        )

    privacy = sections["privacy"]
    if privacy["contains_raw_clinical_text"] is not False:
        raise ValueError("privacy.contains_raw_clinical_text must be false")
    if not isinstance(privacy["omitted_fields"], list) or not privacy["omitted_fields"]:
        raise ValueError("privacy.omitted_fields must be a non-empty array")
    if any(
        not isinstance(value, str) or not value.strip()
        for value in privacy["omitted_fields"]
    ):
        raise ValueError("privacy.omitted_fields must contain non-empty strings")
    if len(privacy["omitted_fields"]) != len(set(privacy["omitted_fields"])):
        raise ValueError("privacy.omitted_fields contains duplicates")
    _string(privacy["omission_rationale"], location="privacy.omission_rationale")
    if privacy["review_status"] not in {
        "aggregate-and-identifiers-only",
        "approved-license-compatible-text",
    }:
        raise ValueError("privacy.review_status is invalid")

    # The canonical encoder is also a fail-closed check for NaN/Infinity and
    # non-JSON values that Python's loose encoder would otherwise accept.
    canonical_json(record)


def canonical_json(record: Any) -> bytes:
    return (
        json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def record_sha256(record: Any) -> str:
    validate_record(record)
    return hashlib.sha256(canonical_json(record)).hexdigest()


def validate_archive(
    records: list[Any], *, expected_experiment_id: str | None = None
) -> None:
    if not isinstance(records, list) or not records:
        raise ValueError("archive must contain at least one record")
    record_ids: set[str] = set()
    question_arms: set[tuple[str, str]] = set()
    experiment_ids: set[str] = set()
    for index, record in enumerate(records):
        try:
            validate_record(record)
        except ValueError as error:
            raise ValueError(f"record[{index}]: {error}") from error
        if record["record_id"] in record_ids:
            raise ValueError(f"duplicate record_id: {record['record_id']}")
        record_ids.add(record["record_id"])
        question_arm = (record["question_id"], record["arm_id"])
        if question_arm in question_arms:
            raise ValueError(f"duplicate question_id/arm_id: {question_arm}")
        question_arms.add(question_arm)
        experiment_ids.add(record["experiment_id"])
    if len(experiment_ids) != 1:
        raise ValueError("archive records must have one experiment_id")
    if expected_experiment_id is not None and experiment_ids != {
        expected_experiment_id
    }:
        raise ValueError(f"archive experiment_id must equal {expected_experiment_id}")


def load_jsonl(path: Path) -> list[Any]:
    records = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            raise ValueError(f"blank JSONL line at {line_number}")
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError(
                f"invalid JSON on line {line_number}: {error.msg}"
            ) from error
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", required=True, type=Path, help="public JSONL archive"
    )
    parser.add_argument("--experiment-id")
    args = parser.parse_args()
    try:
        records = load_jsonl(args.input)
        validate_archive(records, expected_experiment_id=args.experiment_id)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    archive_sha = hashlib.sha256(args.input.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "record_count": len(records),
                "archive_sha256": archive_sha,
                "experiment_id": records[0]["experiment_id"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
