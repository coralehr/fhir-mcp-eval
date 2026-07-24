#!/usr/bin/env python3
"""Recompute the A0/A0-prime/A5 table from the minimized score artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = "a0prime-score-artifact-v1"
DEFAULT_ARTIFACT = (
    Path(__file__).resolve().parent
    / "artifacts"
    / "a0prime-v1"
    / "score-artifact.json"
)


def load_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(artifact, dict):
        raise ValueError("score artifact must be a JSON object")
    if artifact.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported score artifact schema")
    if not isinstance(artifact.get("source_receipt"), dict):
        raise ValueError("score artifact is missing its source receipt")
    questions = artifact.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("score artifact questions must be a non-empty array")
    question_count = artifact.get("question_count")
    if type(question_count) is not int or question_count != len(questions):
        raise ValueError("score artifact question_count does not match questions")
    seen: set[str] = set()
    strata: set[str] = set()
    for index, row in enumerate(questions):
        if not isinstance(row, dict):
            raise ValueError(f"question {index} is not an object")
        qid = row.get("question_id")
        if not isinstance(qid, str) or not qid or qid in seen:
            raise ValueError(f"question {index} has an invalid or duplicate ID")
        seen.add(qid)
        if row.get("stratum") not in {"overflow", "matched"}:
            raise ValueError(f"question {qid} has an invalid stratum")
        strata.add(row["stratum"])
        if not isinstance(row.get("patient_fhir_id"), str):
            raise ValueError(f"question {qid} has no patient ID")
        for field in ("a0_correct", "a5_correct", "a0prime_correct"):
            if row.get(field) not in (0, 1) or type(row[field]) is not int:
                raise ValueError(f"question {qid} has an invalid {field}")
        if type(row.get("a0prime_overflow")) is not bool:
            raise ValueError(f"question {qid} has an invalid overflow flag")
        if row.get("a0prime_grade_source") not in {
            "failure",
            "numeric",
            "panel",
        }:
            raise ValueError(f"question {qid} has an invalid grade source")
    if strata != {"overflow", "matched"}:
        raise ValueError("score artifact must contain both overflow and matched strata")
    return artifact


def _accuracy(
    rows: list[dict[str, Any]], field: str, predicate: Callable[[dict[str, Any]], bool]
) -> tuple[float, int]:
    labels = [int(row[field]) for row in rows if predicate(row)]
    return sum(labels) / len(labels), len(labels)


def render_verdict(artifact: dict[str, Any]) -> str:
    rows = artifact["questions"]

    def overflow(row: dict[str, Any]) -> bool:
        return row["stratum"] == "overflow"

    def matched(row: dict[str, Any]) -> bool:
        return row["stratum"] == "matched"

    def pooled(_row: dict[str, Any]) -> bool:
        return True

    overflow_count = sum(1 for row in rows if overflow(row))
    matched_count = sum(1 for row in rows if matched(row))
    lines = [
        f"{'arm':<14}{f'overflow({overflow_count})':>16}"
        f"{f'matched({matched_count})':>16}{f'pooled({len(rows)})':>16}"
    ]
    for name, field in (
        ("A0 raw", "a0_correct"),
        ("A5 code", "a5_correct"),
        ("A0' projected", "a0prime_correct"),
    ):
        o, on = _accuracy(rows, field, overflow)
        m, mn = _accuracy(rows, field, matched)
        p, pn = _accuracy(rows, field, pooled)
        lines.append(
            f"{name:<14}{o:>13.1%}({on}){m:>13.1%}({mn}){p:>13.1%}({pn})"
        )
    remaining_overflow = sum(
        1
        for row in rows
        if overflow(row) and bool(row["a0prime_overflow"])
    )
    o_a0prime = _accuracy(rows, "a0prime_correct", overflow)[0]
    o_a5 = _accuracy(rows, "a5_correct", overflow)[0]
    recovery = f"{o_a0prime / o_a5:.0%}" if o_a5 else "n/a"
    lines.extend(
        [
            "",
            f"A0' still overflows on {remaining_overflow}/{overflow_count} "
            "of the overflow stratum",
            "A0' non-numeric answers awaiting panel label: 0",
            "",
            f">>> VERDICT: overflow-stratum recovery = A0' {o_a0prime:.1%} "
            f"vs code {o_a5:.1%} vs raw 0%",
            f"   projection-alone recovers {recovery} of the code arm's "
            "overflow-stratum accuracy",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    args = parser.parse_args(argv)
    try:
        artifact = load_artifact(args.artifact)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(render_verdict(artifact))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
