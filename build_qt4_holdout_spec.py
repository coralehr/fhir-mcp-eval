#!/usr/bin/env python3
"""Freeze the untouched QT-4 valid-split holdout from preserved dev receipts.

This tool performs no model calls and never reads answer content to select rows.
The only exclusion is membership in the preserved A6a dev manifest. Ordering is
a deterministic hash of question ID under the registered salt.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a6_packet_builder import (
    MICRO_DISPATCHER_VERSION,
    MICRO_QUESTION_TERMS,
    is_microbiology_question,
)


DEFAULT_ORDER_SALT = "qt4-valid374-20260713:"
SPEC_KIND = "qt4_holdout_question_spec"
SPEC_VERSION = "qt4-valid374-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HoldoutBuild:
    rows: list[dict[str, str]]
    question_ids: list[str]
    microbiology_question_ids: list[str]
    valid_question_count: int
    development_question_count: int
    input_sha256: str
    development_manifest_sha256: str
    development_question_ids_sha256: str
    order_salt: str

    def spec(self) -> dict[str, Any]:
        return {
            "kind": SPEC_KIND,
            "version": SPEC_VERSION,
            "source_split": "valid",
            "selection": "all source-split rows excluding the preserved development manifest IDs",
            "order_method": (
                "ascending sha256('" + self.order_salt + "' + question_id), then question_id"
            ),
            "order_salt": self.order_salt,
            "expected_source_split_question_count": self.valid_question_count,
            "excluded_development_question_count": self.development_question_count,
            "expected_question_count": len(self.question_ids),
            "expected_microbiology_question_count": len(
                self.microbiology_question_ids
            ),
            "expected_non_microbiology_question_count": (
                len(self.question_ids) - len(self.microbiology_question_ids)
            ),
            "micro_dispatcher": {
                "version": MICRO_DISPATCHER_VERSION,
                "question_terms": list(MICRO_QUESTION_TERMS),
                "source_stratum_agreement": "exact on frozen holdout",
            },
            "provenance": {
                "input_sha256": self.input_sha256,
                "development_manifest_sha256": self.development_manifest_sha256,
                "development_question_ids_sha256": self.development_question_ids_sha256,
            },
            "question_ids": self.question_ids,
            "microbiology_question_ids": self.microbiology_question_ids,
        }


def _load_development_manifest(
    path: Path, *, input_sha256: str, split: str
) -> tuple[int, set[str], str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("development manifest must be a JSON object")
    if value.get("kind") != "a6_query_aware_packet_manifest":
        raise ValueError("development manifest kind is not A6 packet manifest")
    config = value.get("config")
    if not isinstance(config, dict):
        raise ValueError("development manifest config is missing")
    if config.get("split") != split or config.get("planner") != "question-only":
        raise ValueError("development manifest is not the question-only valid split")
    manifest_input = value.get("input")
    if not isinstance(manifest_input, dict) or manifest_input.get("sha256") != input_sha256:
        raise ValueError("development manifest input SHA-256 does not match dataset")
    limit = config.get("limit")
    question_count = value.get("questions")
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit < 1
        or question_count != limit
    ):
        raise ValueError("development manifest limit/question count is invalid")
    packet_hashes = value.get("packet_hashes")
    if not isinstance(packet_hashes, dict) or len(packet_hashes) != limit:
        raise ValueError("development manifest packet hashes are incomplete")
    question_ids = {str(question_id) for question_id in packet_hashes}
    if len(question_ids) != limit or any(not question_id for question_id in question_ids):
        raise ValueError("development manifest question IDs are invalid")
    return limit, question_ids, sha256_file(path)


def build_holdout(
    *,
    input_path: Path,
    development_manifest_path: Path,
    split: str = "valid",
    order_salt: str = DEFAULT_ORDER_SALT,
) -> HoldoutBuild:
    input_sha256 = sha256_file(input_path)
    development_count, development_ids, development_manifest_sha256 = (
        _load_development_manifest(
            development_manifest_path,
            input_sha256=input_sha256,
            split=split,
        )
    )
    with input_path.open(newline="", encoding="utf-8") as handle:
        all_rows = list(csv.DictReader(handle))
    rows = [row for row in all_rows if row.get("split") == split]
    question_ids = [str(row.get("question_id") or "") for row in rows]
    if any(not question_id for question_id in question_ids):
        raise ValueError(f"{split} split contains a missing question_id")
    if len(question_ids) != len(set(question_ids)):
        raise ValueError(f"{split} split contains duplicate question IDs")
    expected_development_ids = set(question_ids[:development_count])
    if development_ids != expected_development_ids:
        raise ValueError(
            f"development manifest must contain the first {development_count} {split} rows"
        )

    holdout_rows = [row for row in rows if row["question_id"] not in development_ids]
    mismatches = [
        row["question_id"]
        for row in holdout_rows
        if is_microbiology_question(row.get("question"))
        != (str(row.get("main_table_name") or "").strip().lower() == "microbiologyevents")
    ]
    if mismatches:
        raise ValueError(
            "question-only dispatcher and source stratum disagree on "
            f"{len(mismatches)} holdout rows"
        )

    holdout_rows.sort(
        key=lambda row: (
            hashlib.sha256(
                f"{order_salt}{row['question_id']}".encode("utf-8")
            ).hexdigest(),
            row["question_id"],
        )
    )
    holdout_ids = [row["question_id"] for row in holdout_rows]
    microbiology_ids = [
        row["question_id"]
        for row in holdout_rows
        if is_microbiology_question(row.get("question"))
    ]
    return HoldoutBuild(
        rows=holdout_rows,
        question_ids=holdout_ids,
        microbiology_question_ids=microbiology_ids,
        valid_question_count=len(rows),
        development_question_count=development_count,
        input_sha256=input_sha256,
        development_manifest_sha256=development_manifest_sha256,
        development_question_ids_sha256=sha256_json(sorted(development_ids)),
        order_salt=order_salt,
    )


def write_holdout_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError("cannot write an empty holdout")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--development-manifest", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-spec", type=Path, required=True)
    parser.add_argument("--split", default="valid")
    parser.add_argument("--order-salt", default=DEFAULT_ORDER_SALT)
    args = parser.parse_args()

    result = build_holdout(
        input_path=args.input,
        development_manifest_path=args.development_manifest,
        split=args.split,
        order_salt=args.order_salt,
    )
    write_holdout_csv(args.output_csv, result.rows)
    args.output_spec.parent.mkdir(parents=True, exist_ok=True)
    args.output_spec.write_text(
        json.dumps(result.spec(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "holdout_questions": len(result.question_ids),
                "microbiology_questions": len(result.microbiology_question_ids),
                "non_microbiology_questions": (
                    len(result.question_ids) - len(result.microbiology_question_ids)
                ),
                "output_csv_sha256": sha256_file(args.output_csv),
                "output_spec_sha256": sha256_file(args.output_spec),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
