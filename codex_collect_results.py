#!/usr/bin/env python3
"""Convert a Codex harness run directory into score_taxonomy.py input JSON."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import codex_harness


REPO_ROOT = Path(__file__).resolve().parent
REQUIRED_ANSWER_FIELDS = {"answer", "source_resource_ids", "evidence_summary", "insufficiency_reason"}


def resolve_repo_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def load_rows(input_path: Path) -> dict[str, dict[str, Any]]:
    with input_path.open(newline="", encoding="utf-8") as f:
        return {str(row.get("question_id")): row for row in csv.DictReader(f)}


def load_summary(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "summary.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("questions") or [])
    questions = []
    for answer_path in sorted((run_dir / "questions").glob("*/answer.json")):
        questions.append(
            {
                "question_id": answer_path.parent.name,
                "status": "unknown",
                "returncode": None,
                "answer_path": str(answer_path),
                "event_log_path": str(answer_path.with_name("events.jsonl")),
            }
        )
    return questions


def parse_answer(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, "missing_answer"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"answer_parse_error: {exc}"
    if not isinstance(data, dict):
        return None, "answer_not_object"
    schema_error = validate_answer_object(data)
    if schema_error:
        return None, f"answer_schema_error: {schema_error}"
    return data, None


def validate_answer_object(data: dict[str, Any]) -> str | None:
    missing = sorted(REQUIRED_ANSWER_FIELDS - set(data))
    if missing:
        return "missing " + ",".join(missing)
    if not isinstance(data.get("answer"), str):
        return "answer must be string"
    if not isinstance(data.get("source_resource_ids"), list):
        return "source_resource_ids must be list"
    if not all(isinstance(item, str) for item in data.get("source_resource_ids", [])):
        return "source_resource_ids items must be strings"
    if not isinstance(data.get("evidence_summary"), str):
        return "evidence_summary must be string"
    insufficiency_reason = data.get("insufficiency_reason")
    if insufficiency_reason is not None and not isinstance(insufficiency_reason, str):
        return "insufficiency_reason must be string or null"
    return None


def normalize_resource_ids(source_resource_ids: Any) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    if not isinstance(source_resource_ids, list):
        return grouped
    for raw in source_resource_ids:
        text = str(raw or "").strip()
        if not text:
            continue
        if "/" in text:
            resource_type, ident = text.split("/", 1)
        else:
            resource_type, ident = "Unknown", text
        if not resource_type or not ident:
            continue
        grouped.setdefault(resource_type, [])
        if ident not in grouped[resource_type]:
            grouped[resource_type].append(ident)
    return {k: sorted(v) for k, v in sorted(grouped.items())}


def normalize_usage(usage: dict[str, Any]) -> dict[str, int]:
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    total = usage.get("total_tokens", 0) or 0
    try:
        prompt_i = int(prompt)
        completion_i = int(completion)
        total_i = int(total) if total else prompt_i + completion_i
    except Exception:
        return {}
    out = {"prompt_tokens": prompt_i, "completion_tokens": completion_i, "total_tokens": total_i}
    if "cost" in usage:
        try:
            out["cost_micros"] = int(float(usage["cost"]) * 1_000_000)
        except Exception:
            pass
    return out


def extract_usage(event_log_path: Path | None) -> dict[str, int] | None:
    if not event_log_path or not event_log_path.exists():
        return None
    last_usage: dict[str, Any] | None = None
    for line in event_log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        candidates = [
            event.get("usage") if isinstance(event, dict) else None,
            event.get("msg", {}).get("usage") if isinstance(event.get("msg"), dict) else None,
            event.get("message", {}).get("usage") if isinstance(event.get("message"), dict) else None,
        ]
        for candidate in candidates:
            if isinstance(candidate, dict):
                last_usage = candidate
    if not last_usage:
        return None
    normalized = normalize_usage(last_usage)
    return normalized or None


def resolve_recorded_path(run_dir: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    repo_relative = REPO_ROOT / path
    if repo_relative.exists():
        return repo_relative
    return run_dir / path


def summary_answer_path(run_dir: Path, item: dict[str, Any], qid: str) -> Path:
    if item.get("answer_path"):
        return resolve_recorded_path(run_dir, Path(str(item["answer_path"])))
    return run_dir / "questions" / codex_harness.slugify(qid) / "answer.json"


def summary_event_log_path(run_dir: Path, item: dict[str, Any], qid: str) -> Path:
    if item.get("event_log_path"):
        return resolve_recorded_path(run_dir, Path(str(item["event_log_path"])))
    return run_dir / "questions" / codex_harness.slugify(qid) / "events.jsonl"


def build_result_record(row: dict[str, Any], item: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    qid = str(row.get("question_id"))
    answer_path = summary_answer_path(run_dir, item, qid)
    event_log_path = summary_event_log_path(run_dir, item, qid)
    answer, parse_error = parse_answer(answer_path)
    status = item.get("status")
    returncode = item.get("returncode")
    errors = []
    if status not in (None, "ok", "skipped", "unknown"):
        errors.append(str(status))
    if returncode not in (None, 0):
        errors.append(f"returncode={returncode}")
    if parse_error:
        errors.append(parse_error)

    answer_text = str(answer.get("answer") or "") if answer else ""
    trace = [
        {
            "role": "system",
            "content": "Codex harness run",
            "prompt_path": str(answer_path.with_name("prompt.txt")),
            "event_log_path": str(event_log_path),
        }
    ]
    if answer and answer.get("evidence_summary"):
        trace.append({"role": "assistant", "content": str(answer["evidence_summary"])})
    if answer and answer.get("insufficiency_reason"):
        trace.append({"role": "assistant", "content": "insufficiency_reason: " + str(answer["insufficiency_reason"])})

    return {
        "question_id": row.get("question_id"),
        "question": row.get("question"),
        "true_answer": row.get("true_answer"),
        "true_fhir_ids": row.get("true_fhir_ids"),
        "patient_fhir_id": row.get("patient_fhir_id"),
        "agent_answer": answer_text,
        "agent_fhir_resources": normalize_resource_ids(answer.get("source_resource_ids") if answer else []),
        "trace": trace,
        "usage": extract_usage(event_log_path),
        "error": "; ".join(errors),
    }


def collect_results(*, input_path: Path, run_dir: Path, question_ids: set[str] | None = None) -> list[dict[str, Any]]:
    rows = load_rows(input_path)
    summary_items = load_summary(run_dir)
    out = []
    for item in summary_items:
        qid = str(item.get("question_id"))
        if question_ids and qid not in question_ids:
            continue
        row = rows.get(qid)
        if not row:
            out.append(
                {
                    "question_id": qid,
                    "question": None,
                    "true_answer": None,
                    "true_fhir_ids": None,
                    "patient_fhir_id": None,
                    "agent_answer": "",
                    "agent_fhir_resources": {},
                    "trace": [],
                    "usage": None,
                    "error": "question_not_found_in_input",
                }
            )
            continue
        out.append(build_result_record(row, item, run_dir))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Codex harness answers into score_taxonomy input JSON.")
    parser.add_argument("--input", type=Path, default=Path("final_dataset/full_test409.csv"))
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--question-id", action="append", default=[])
    args = parser.parse_args()

    input_path = resolve_repo_path(args.input)
    run_dir = resolve_repo_path(args.run_dir)
    output = resolve_repo_path(args.output)
    records = collect_results(
        input_path=input_path,
        run_dir=run_dir,
        question_ids=set(args.question_id) if args.question_id else None,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "records": len(records)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
