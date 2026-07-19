#!/usr/bin/env python3
"""Regenerate the qid-level A0-prime overflow failure decomposition."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import a0prime_verdict


SCHEMA_VERSION = "a0prime-failure-decomposition-v1"
DEFAULT_JSON = (
    Path(__file__).resolve().parent
    / "artifacts"
    / "a0prime-v1"
    / "failure-decomposition.json"
)
DEFAULT_MARKDOWN = DEFAULT_JSON.with_suffix(".md")
FEATURE_FIELDS = {
    "a0prime_cap_drop_language",
    "a0prime_earliest_or_first",
    "a0prime_repeated_resource_type",
}
TOKEN_FIELD = "a0prime_max_tool_content_cl100k_tokens"


def compute(artifact_path: Path) -> dict[str, Any]:
    payload = artifact_path.read_bytes()
    artifact = a0prime_verdict.load_artifact(artifact_path)
    overflow_rows = [
        row for row in artifact["questions"] if row["stratum"] == "overflow"
    ]
    questions: list[dict[str, Any]] = []
    for row in overflow_rows:
        missing = FEATURE_FIELDS - set(row)
        if missing:
            raise ValueError(
                f"question {row['question_id']} lacks decomposition fields: "
                f"{sorted(missing)}"
            )
        if any(type(row[field]) is not bool for field in FEATURE_FIELDS):
            raise ValueError(
                f"question {row['question_id']} has a non-boolean decomposition field"
            )
        token_count = row.get(TOKEN_FIELD)
        if type(token_count) is not int or token_count < 0:
            raise ValueError(
                f"question {row['question_id']} lacks a non-negative {TOKEN_FIELD}"
            )
        if row["a0prime_correct"] and row["a0prime_overflow"]:
            raise ValueError(
                f"question {row['question_id']} is both correct and overflowed"
            )
        if row["a0prime_correct"]:
            outcome = "correct"
        elif row["a0prime_overflow"]:
            outcome = "still_overflow"
        else:
            outcome = "fit_but_wrong"
        questions.append(
            {
                "a5_correct": row["a5_correct"],
                "cap_drop_language": outcome == "fit_but_wrong"
                and row["a0prime_cap_drop_language"],
                "earliest_or_first": outcome == "fit_but_wrong"
                and row["a0prime_earliest_or_first"],
                "max_tool_content_cl100k_tokens": token_count,
                "outcome": outcome,
                "patient_fhir_id": row["patient_fhir_id"],
                "question_id": row["question_id"],
                "repeated_resource_overflow": outcome == "still_overflow"
                and row["a0prime_repeated_resource_type"],
            }
        )

    def count(field: str) -> int:
        return sum(bool(row[field]) for row in questions)

    def outcome_count(value: str) -> int:
        return sum(row["outcome"] == value for row in questions)

    counts = {
        "cap_drop_language": count("cap_drop_language"),
        "correct": outcome_count("correct"),
        "earliest_or_first": count("earliest_or_first"),
        "fit_but_wrong": outcome_count("fit_but_wrong"),
        "repeated_resource_overflow": count("repeated_resource_overflow"),
        "still_overflow": outcome_count("still_overflow"),
    }
    if counts["correct"] + counts["still_overflow"] + counts["fit_but_wrong"] != len(questions):
        raise ValueError("primary outcomes do not partition the overflow stratum")

    def recovered(field: str) -> int:
        return sum(bool(row[field]) and row["a5_correct"] == 1 for row in questions)

    still_overflow_tokens = [
        row["max_tool_content_cl100k_tokens"]
        for row in questions
        if row["outcome"] == "still_overflow"
    ]

    return {
        "code_recovery": {
            "cap_drop_language": recovered("cap_drop_language"),
            "still_overflow": sum(
                row["outcome"] == "still_overflow" and row["a5_correct"] == 1
                for row in questions
            ),
        },
        "counts": counts,
        "definitions": {
            "cap_drop_language": (
                "fit-but-wrong answer contains cannot find, cannot determine, "
                "or truncat (case-insensitive)"
            ),
            "earliest_or_first": (
                "fit-but-wrong question contains the whole word earliest or first"
            ),
            "repeated_resource_overflow": (
                "still-overflow trace requests the same resource_type more than once "
                "through get_resources_by_patient_fhir_id"
            ),
            "single_tool_block_tokens": (
                "maximum cl100k_base token count among string contents of trace "
                "messages whose role is tool"
            ),
        },
        "overflow_question_count": len(questions),
        "questions": questions,
        "schema_version": SCHEMA_VERSION,
        "score_artifact_sha256": hashlib.sha256(payload).hexdigest(),
        "single_tool_block_tokens": {
            "encoding": "cl100k_base",
            "max_tokens": max(still_overflow_tokens, default=0),
            "over_32000": sum(value > 32_000 for value in still_overflow_tokens),
            "question_count": len(still_overflow_tokens),
        },
    }


def render_markdown(result: dict[str, Any]) -> str:
    counts = result["counts"]
    recovery = result["code_recovery"]
    total = result["overflow_question_count"]
    token_receipt = result["single_tool_block_tokens"]
    lines = [
        "# A0-prime overflow failure decomposition",
        "",
        f"Scope: the {total} questions where A0 raw overflowed.",
        "",
        "| category | count |",
        "|---|---:|",
        f"| correct | {counts['correct']} |",
        f"| still overflow | {counts['still_overflow']} |",
        f"| fit but wrong | {counts['fit_but_wrong']} |",
        f"| cap-drop language | {counts['cap_drop_language']} |",
        f"| earliest/first among fit-but-wrong | {counts['earliest_or_first']} |",
        (
            "| repeated-resource overflow | "
            f"{counts['repeated_resource_overflow']} |"
        ),
        "",
        "The three primary outcomes (correct, still overflow, fit but wrong) are "
        "mutually exclusive. The final three categories are diagnostic subsets.",
        "",
        "## Single-block token check",
        "",
        (
            f"Among {token_receipt['question_count']} still-overflow questions, "
            f"{token_receipt['over_32000']} contain an individual tool block over "
            f"32,000 `{token_receipt['encoding']}` tokens; the maximum is "
            f"{token_receipt['max_tokens']:,} tokens."
        ),
        "",
        "## Code-arm recovery",
        "",
        f"- Cap-drop-language cases recovered by A5: "
        f"{recovery['cap_drop_language']}/{counts['cap_drop_language']}.",
        f"- Still-overflow cases recovered by A5: "
        f"{recovery['still_overflow']}/{counts['still_overflow']}.",
        "",
        "## Deterministic definitions",
        "",
    ]
    for name, definition in result["definitions"].items():
        lines.append(f"- `{name}`: {definition}.")
    lines.extend(
        [
            "",
            f"Score artifact SHA-256: `{result['score_artifact_sha256']}`.",
            "",
            "The companion JSON contains every question ID and its category flags.",
        ]
    )
    return "\n".join(lines) + "\n"


def _canonical_line(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact",
        type=Path,
        default=a0prime_verdict.DEFAULT_ARTIFACT,
    )
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-out", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args(argv)
    try:
        result = compute(args.artifact)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_bytes(_canonical_line(result))
    args.markdown_out.write_text(render_markdown(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
