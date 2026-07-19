#!/usr/bin/env python3
"""Build the minimized A0/A0-prime/A5 scoring artifact from raw runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "a0prime-score-artifact-v1"
NUMBER = re.compile(r"-?\d+\.?\d*")
FAILURE = re.compile(
    r"Input tokens exceeded|Max retries|RateLimitError|"
    r"exceeded your current quota|Expected .* tool call, but got|Traceback",
    re.IGNORECASE,
)
VOTE_NAME = re.compile(r"p[123]_b[0-9][0-9]\.json")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _source_entry(root: Path, path: Path) -> dict[str, Any]:
    return {
        "bytes": path.stat().st_size,
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
    }


def _by_question(path: Path) -> dict[str, dict[str, Any]]:
    records = _load(path)
    if not isinstance(records, list):
        raise ValueError(f"{path}: expected an answer array")
    by_id: dict[str, dict[str, Any]] = {}
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get("question_id"), str):
            raise ValueError(f"{path}: malformed answer record")
        qid = row["question_id"]
        if qid in by_id:
            raise ValueError(f"{path}: duplicate question ID {qid}")
        by_id[qid] = row
    return by_id


def _panel_labels(paths: list[Path]) -> dict[str, int]:
    votes: dict[str, list[int]] = defaultdict(list)
    for path in paths:
        batches = _load(path)
        if not isinstance(batches, list):
            raise ValueError(f"{path}: expected a panel batch array")
        for batch in batches:
            for judge in batch.get("votes", []):
                for row in judge:
                    qid = row.get("qid")
                    for arm in ("resource", "code"):
                        label = row.get(arm)
                        if isinstance(qid, str) and label in (0, 1):
                            votes[f"{arm}|{qid}"].append(label)
    labels: dict[str, int] = {}
    for key, values in votes.items():
        if sum(values) * 2 == len(values):
            raise ValueError(f"tied panel label for {key}")
        labels[key] = int(sum(values) * 2 > len(values))
    return labels


def _a0prime_panel_labels(paths: list[Path]) -> dict[str, int]:
    votes: dict[str, list[int]] = defaultdict(list)
    for path in paths:
        data = _load(path)
        grades = data.get("grades") if isinstance(data, dict) else None
        if not isinstance(grades, list):
            raise ValueError(f"{path}: missing grades array")
        seen: set[str] = set()
        for row in grades:
            qid = row.get("qid") if isinstance(row, dict) else None
            label = row.get("label") if isinstance(row, dict) else None
            if not isinstance(qid, str) or label not in (0, 1) or qid in seen:
                raise ValueError(f"{path}: malformed or duplicate grade row")
            seen.add(qid)
            votes[qid].append(label)
    labels: dict[str, int] = {}
    for qid, values in votes.items():
        if len(values) != 3:
            raise ValueError(f"A0-prime panel requires exactly three votes for {qid}")
        labels[qid] = int(sum(values) * 2 > len(values))
    return labels


def _gold_type(gold: str | None) -> str:
    value = (gold or "").strip()
    if value in ("[[1]]", "[[0]]"):
        return "boolean"
    if "'" in value or '"' in value:
        return "categorical"
    tokens = [
        token
        for token in re.split(r"[\s,]+", re.sub(r"[\[\]]", " ", value))
        if token
    ]
    if tokens and all(re.fullmatch(r"-?\d+\.?\d*", token) for token in tokens):
        return "numeric"
    return "other"


def _is_failure(record: dict[str, Any]) -> bool:
    answer = record.get("agent_answer") or ""
    return not answer.strip() or bool(FAILURE.search(answer))


def _numeric_label(answer: str | None, gold: str | None) -> int:
    gold_values = [float(value) for value in NUMBER.findall(gold or "")]
    answer_values = [float(value) for value in NUMBER.findall(answer or "")]
    if not gold_values or not answer_values:
        return 0
    return int(
        all(
            any(
                abs(answer_value - gold_value)
                <= max(0.05, 0.01 * abs(gold_value))
                for answer_value in answer_values
            )
            for gold_value in gold_values
        )
    )


def build_artifact(source_root: Path) -> dict[str, Any]:
    root = source_root.resolve(strict=True)
    full = root / "runs" / "full409"
    projected_root = root / "runs" / "a0prime"
    paths = {
        "resource": full / "multi_turn_resource.json",
        "code": full / "multi_turn_code_resource.json",
        "projected": projected_root / "multi_turn_projected_resource.json",
        "deterministic": full / "det_labels.json",
        "panel": full / "panel_votes.json",
        "panel_new": full / "panel_votes_new.json",
        "strata": full / "_strata.json",
    }
    vote_dir = projected_root / "codex_votes"
    vote_paths = sorted(path for path in vote_dir.glob("*.json") if VOTE_NAME.fullmatch(path.name))
    extras = sorted(path for path in vote_dir.glob("*.json") if path not in vote_paths)
    if extras:
        raise ValueError(f"unexpected A0-prime vote files: {[path.name for path in extras]}")
    if not vote_paths:
        raise ValueError("missing A0-prime panel vote files")

    resource = _by_question(paths["resource"])
    code = _by_question(paths["code"])
    projected = _by_question(paths["projected"])
    deterministic = _load(paths["deterministic"])
    if not isinstance(deterministic, dict):
        raise ValueError("deterministic labels must be an object")
    original_panel = _panel_labels([paths["panel"], paths["panel_new"]])
    projected_panel = _a0prime_panel_labels(vote_paths)
    strata = _load(paths["strata"])
    ids = strata.get("ids") if isinstance(strata, dict) else None
    overflow_ids = strata.get("overflow") if isinstance(strata, dict) else None
    matched_ids = strata.get("matched") if isinstance(strata, dict) else None
    if not all(isinstance(value, list) for value in (ids, overflow_ids, matched_ids)):
        raise ValueError("strata artifact is malformed")
    if set(ids) != set(overflow_ids) | set(matched_ids) or set(overflow_ids) & set(matched_ids):
        raise ValueError("strata do not partition the scheduled questions")
    if not (set(ids) == set(resource) == set(code) == set(projected)):
        raise ValueError("answer arms do not match the scheduled question set")

    questions: list[dict[str, Any]] = []
    overflow_set = set(overflow_ids)
    for qid in ids:
        arm_records = (resource[qid], code[qid], projected[qid])
        patient_ids = {record.get("patient_fhir_id") for record in arm_records}
        if len(patient_ids) != 1 or not isinstance(next(iter(patient_ids)), str):
            raise ValueError(f"patient identity mismatch for {qid}")

        def original_label(arm: str) -> int:
            key = f"{arm}|{qid}"
            label = deterministic.get(key, original_panel.get(key))
            if label not in (0, 1):
                raise ValueError(f"missing original-arm label for {key}")
            return int(label)

        projected_record = projected[qid]
        if _is_failure(projected_record):
            projected_label = 0
            grade_source = "failure"
        elif _gold_type(projected_record.get("true_answer")) == "numeric":
            projected_label = _numeric_label(
                projected_record.get("agent_answer"),
                projected_record.get("true_answer"),
            )
            grade_source = "numeric"
        else:
            if qid not in projected_panel:
                raise ValueError(f"missing A0-prime panel label for {qid}")
            projected_label = projected_panel[qid]
            grade_source = "panel"
        questions.append(
            {
                "a0_correct": original_label("resource"),
                "a0prime_correct": projected_label,
                "a0prime_grade_source": grade_source,
                "a0prime_overflow": "Input tokens exceeded"
                in (projected_record.get("agent_answer") or ""),
                "a5_correct": original_label("code"),
                "patient_fhir_id": next(iter(patient_ids)),
                "question_id": qid,
                "stratum": "overflow" if qid in overflow_set else "matched",
            }
        )

    all_sources = [*paths.values(), *vote_paths]
    return {
        "question_count": len(questions),
        "questions": questions,
        "schema_version": SCHEMA_VERSION,
        "source_receipt": {
            "algorithm": "sha256",
            "files": sorted(
                (_source_entry(root, path) for path in all_sources),
                key=lambda item: item["path"],
            ),
        },
    }


def _canonical_line(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    artifact = build_artifact(args.source_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(_canonical_line(artifact))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
