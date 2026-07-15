#!/usr/bin/env python3
"""Exact-byte, arm-label-blind answer harness for sealed A11 prompts.

The packet record carries the already-compiled ``model_payload_json`` string
and the fully materialized ``prompt_text``. This module validates the payload
without reserializing it, proves the sealed prompt is the frozen envelope with
those payload bytes inserted verbatim, and sends those sealed prompt bytes to
the unchanged ``codex_harness`` runtime and integrity helpers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Iterable

import codex_harness


PROMPT_PROTOCOL_VERSION = "a11-exact-payload-prompt-v1"
PROMPT_RECORD_VERSION = "a11-answer-prompt-record-v1"
INPUT_FIELDS = frozenset({"question_id", "question", "assumption"})
PROMPT_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "question_id",
        "model_payload_json",
        "model_payload_sha256",
        "model_payload_utf8_bytes",
        "prompt_text",
        "prompt_sha256",
    }
)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_FORBIDDEN_PAYLOAD_FIELDS = frozenset(
    {
        # Gold, labels, and audit-only expected values.
        "answer",
        "answerable",
        "expected_answer",
        "expected_event_root",
        "expected_evidence",
        "expected_evidence_refs",
        "expected_root",
        "failure_mode",
        "gold",
        "gold_answer",
        "label",
        "nonselected_reference_answer",
        "nonselected_terminal_resource_ref",
        "reference_answer",
        "selected_root_ref",
        "source_resource_ids",
        "terminal_resource_ref",
        "true_answer",
        "true_fhir_ids",
        # Authorization, governance, and source identity.
        "allowed_purposes",
        "authorization",
        "authorization_context",
        "governed_receipt",
        "integrity",
        "model_packets",
        "patient_ref",
        "patient_fhir_id",
        "policy",
        "policy_context",
        "practice",
        "practice_id",
        "principal",
        "principal_id",
        "purpose",
        "source_epoch",
        "source_id",
        "source_queries",
        "source_resource_ids",
        "source_snapshot",
        "source_version",
        "shared_retrieval_source_sha256",
        # Experiment controls and explicit arm identity.
        "arm",
        "arm_id",
        "arm_label",
        "arm_name",
        "baseline",
        "bounds",
        "condition_label",
        "control",
        "control_label",
        "dispatch",
        "features",
        "kind",
        "max_depth",
        "max_edges",
        "max_packet_bytes",
        "max_paths",
        "max_targets",
        "plan_only",
        "question_id",
        "receipt_hash",
        "retrieval_receipt",
        "root_refs",
        "traversal",
        "treatment",
        "treatment_id",
        "treatment_label",
        "treatment_name",
        "variant",
        "v",
        "t",
        "e",
    }
)
_FORBIDDEN_ARM_VALUES = frozenset(
    {
        "v",
        "t",
        "e",
        "arm-v",
        "arm-t",
        "arm-e",
        "v-arm",
        "t-arm",
        "e-arm",
        "a11-v",
        "a11-t",
        "a11-e",
    }
)

_PROMPT_SUFFIX = (
    b"\n\nUse this packet as read-only evidence. Do not request external data.\n"
    b"Do not call tools, execute commands, inspect the filesystem, or read files; "
    b"any such event invalidates the answer.\n"
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = child
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _loads_json(value: str, *, label: str) -> Any:
    try:
        return json.loads(
            value,
            object_pairs_hook=_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"invalid JSON in {label}") from exc


def _normalized_field(value: str) -> str:
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", value)
    return re.sub(r"[^a-z0-9]+", "_", snake.lower()).strip("_")


def _reject_payload_leakage(value: Any, *, path: str = "payload") -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = _normalized_field(raw_key)
            if (
                key in _FORBIDDEN_PAYLOAD_FIELDS
                or key.startswith(("gold_", "expected_", "true_"))
                or key.endswith(("_arm", "_arm_label"))
            ):
                raise ValueError(f"forbidden model payload field at {path}.{raw_key}")
            _reject_payload_leakage(child, path=f"{path}.{raw_key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_payload_leakage(child, path=f"{path}[{index}]")
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite model payload number at {path}")
    if isinstance(value, str) and value.strip().lower() in _FORBIDDEN_ARM_VALUES:
        raise ValueError(f"forbidden model payload arm label at {path}")


def _validate_input_row(row: dict[str, Any]) -> dict[str, str]:
    if set(row) != INPUT_FIELDS:
        raise ValueError(
            "A11 input row fields must be exactly question_id, question, assumption"
        )
    if any(not isinstance(row[field], str) for field in INPUT_FIELDS):
        raise ValueError("A11 input row values must be strings")
    question_id = row["question_id"].strip()
    question = row["question"].strip()
    if not question_id or not question:
        raise ValueError("question_id and question must be non-empty")
    if codex_harness.slugify(question_id) != question_id:
        raise ValueError("question_id is not path-safe")
    return {
        "question_id": question_id,
        "question": row["question"],
        "assumption": row["assumption"],
    }


def _prompt_prefix(row: dict[str, Any]) -> bytes:
    safe = _validate_input_row(row)
    return (
        "You are running a reproducible FHIR-AgentBench A11 evaluation as the "
        "answering agent.\n"
        "Return only the JSON object required by the provided output schema.\n"
        "Do not use hidden knowledge, training-memory facts, or guesses. Answer "
        "only from the supplied packet.\n"
        "If the data is insufficient, set answer to an explicit insufficiency "
        "statement and explain the missing evidence.\n"
        f"Prompt protocol: {PROMPT_PROTOCOL_VERSION}\n\n"
        f"Question ID: {safe['question_id']}\n\n"
        f"Question:\n{safe['question']}\n\n"
        f"Assumption (authoritative):\n{safe['assumption']}\n\n"
        "Frozen clinical packet:\n"
    ).encode("utf-8")


def render_prompt_bytes(row: dict[str, Any], model_payload_json: str) -> bytes:
    """Insert the supplied payload string without parsing or reserialization."""

    if not isinstance(model_payload_json, str):
        raise ValueError("model_payload_json must be a string")
    payload_bytes = model_payload_json.encode("utf-8", errors="strict")
    return _prompt_prefix(row) + payload_bytes + _PROMPT_SUFFIX


def build_verified_prompt(
    row: dict[str, Any], record: dict[str, Any]
) -> bytes:
    """Validate one sealed record and return its exact prompt bytes."""

    safe = _validate_input_row(row)
    if not isinstance(record, dict) or set(record) != PROMPT_RECORD_FIELDS:
        raise ValueError("A11 prompt record fields changed")
    if record.get("schema_version") != PROMPT_RECORD_VERSION:
        raise ValueError("A11 prompt record schema changed")
    if record.get("question_id") != safe["question_id"]:
        raise ValueError("A11 prompt record question binding changed")
    payload = record.get("model_payload_json")
    if not isinstance(payload, str):
        raise ValueError("model_payload_json must be a string")
    payload_bytes = payload.encode("utf-8", errors="strict")
    payload_sha = record.get("model_payload_sha256")
    payload_byte_count = record.get("model_payload_utf8_bytes")
    prompt_text = record.get("prompt_text")
    prompt_sha = record.get("prompt_sha256")
    if (
        not isinstance(payload_sha, str)
        or _SHA256.fullmatch(payload_sha) is None
        or payload_sha != _sha256(payload_bytes)
    ):
        raise ValueError("A11 model payload hash mismatch")
    if type(payload_byte_count) is not int or payload_byte_count != len(payload_bytes):
        raise ValueError("A11 model payload byte count mismatch")
    if not isinstance(prompt_text, str):
        raise ValueError("A11 sealed prompt_text must be a string")
    if not isinstance(prompt_sha, str) or _SHA256.fullmatch(prompt_sha) is None:
        raise ValueError("A11 sealed prompt hash is invalid")

    parsed = _loads_json(payload, label="model_payload_json")
    if not isinstance(parsed, dict):
        raise ValueError("A11 model payload must be a JSON object")
    _reject_payload_leakage(parsed)

    sealed_prompt = prompt_text.encode("utf-8", errors="strict")
    if _sha256(sealed_prompt) != prompt_sha:
        raise ValueError("A11 sealed prompt hash mismatch")
    expected_prompt = render_prompt_bytes(safe, payload)
    if sealed_prompt != expected_prompt:
        raise ValueError("A11 sealed prompt bytes do not match the frozen envelope")
    return sealed_prompt


def load_input_rows(
    path: Path,
    *,
    limit: int | None = None,
    question_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or set(reader.fieldnames) != INPUT_FIELDS:
            raise ValueError(
                "A11 input CSV fields must be exactly question_id, question, assumption"
            )
        rows = [_validate_input_row(dict(row)) for row in reader]
    by_id = {row["question_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("A11 input contains duplicate question_id values")
    if question_ids is not None:
        missing = sorted(question_ids - set(by_id))
        if missing:
            raise ValueError(f"A11 input is missing requested question IDs: {missing}")
        rows = [row for row in rows if row["question_id"] in question_ids]
    if limit is not None:
        rows = rows[:limit]
    return rows


def load_prompt_records(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = _loads_json(line, label=f"{path}:{line_number}")
            if not isinstance(value, dict) or set(value) != PROMPT_RECORD_FIELDS:
                raise ValueError(f"A11 prompt record fields changed at line {line_number}")
            question_id = value.get("question_id")
            if not isinstance(question_id, str) or not question_id:
                raise ValueError(f"A11 prompt record has no question_id at line {line_number}")
            if question_id in records:
                raise ValueError(f"duplicate A11 prompt record: {question_id}")
            records[question_id] = value
    if not records:
        raise ValueError("A11 prompt record file is empty")
    return records


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _load_summary_items(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    value = _loads_json(path.read_text(encoding="utf-8"), label=str(path))
    questions = value.get("questions") if isinstance(value, dict) else None
    if not isinstance(questions, list):
        raise ValueError("existing A11 summary.json has an invalid shape")
    result: dict[str, dict[str, Any]] = {}
    for item in questions:
        if not isinstance(item, dict) or not isinstance(item.get("question_id"), str):
            raise ValueError("existing A11 summary question is malformed")
        result[item["question_id"]] = item
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["packet"], required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--packet-json", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("runs/codex-a11"))
    parser.add_argument(
        "--schema", type=Path, default=Path("schemas/codex_answer.schema.json")
    )
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--reasoning-effort",
        default=None,
        choices=["low", "medium", "high", "xhigh"],
    )
    parser.add_argument("--profile", default=None)
    parser.add_argument("--substrate", default="codex_subscription")
    parser.add_argument("--sandbox", default="read-only")
    parser.add_argument("--approval", default="never")
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"))
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--question-id", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allow-full-run", action="store_true")
    parser.add_argument("--allow-public-artifact", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.dry_run and not args.live:
        parser.error("live Codex runs require --live; use --dry-run for artifacts")
    if not args.dry_run and not args.allow_full_run and args.limit is None and not args.question_id:
        parser.error("unbounded live Codex runs require --allow-full-run or --question-id")
    codex_harness.validate_out_dir(
        args.out_dir, allow_public_artifact=args.allow_public_artifact
    )

    requested = set(args.question_id) if args.question_id else None
    try:
        rows = load_input_rows(
            args.input,
            limit=args.limit,
            question_ids=requested,
        )
        records = load_prompt_records(args.packet_json)
        missing = [row["question_id"] for row in rows if row["question_id"] not in records]
        if missing:
            raise ValueError(f"A11 prompt records are missing question IDs: {missing}")
        prompts = {
            row["question_id"]: build_verified_prompt(row, records[row["question_id"]])
            for row in rows
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    repo = Path(__file__).resolve().parent
    run_config = {
        "mode": "packet",
        "substrate": args.substrate,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "profile": args.profile,
        "sandbox": args.sandbox,
        "approval": args.approval,
        "packet_cwd_isolated": True,
        "dry_run": args.dry_run,
        "live": args.live,
        "question_count": len(rows),
        "timeout_seconds": args.timeout,
        "codex_bin": args.codex_bin,
        "ignore_user_config": True,
        "ignore_rules": True,
        "prompt_protocol_version": PROMPT_PROTOCOL_VERSION,
        "prompt_record_version": PROMPT_RECORD_VERSION,
        "exact_model_payload_bytes": True,
    }
    git_commit, git_dirty = codex_harness.git_commit_and_dirty(repo)
    manifest = codex_harness.write_manifest(
        manifest_path=args.out_dir / "manifest.json",
        run_config=run_config,
        files={
            "input": args.input,
            "packet_json": args.packet_json,
            "schema": args.schema,
            "harness": Path(__file__).resolve(),
            "runtime_helpers": Path(codex_harness.__file__).resolve(),
        },
        codex_version=codex_harness.run_version(args.codex_bin),
        git_commit=git_commit,
        git_dirty=git_dirty,
    )

    summary_path = args.out_dir / "summary.json"
    summary_by_id = _load_summary_items(summary_path)
    current_summary: list[dict[str, Any]] = []

    def record(item: dict[str, Any]) -> None:
        current_summary.append(item)
        summary_by_id[str(item["question_id"])] = item

    for row in rows:
        question_id = row["question_id"]
        prompt_bytes = prompts[question_id]
        prompt = prompt_bytes.decode("utf-8", errors="strict")
        paths = codex_harness.paths_for_question(args.out_dir, question_id)
        contamination_path = paths.answer_path.with_name("contamination.json")
        stale_artifact_path = paths.answer_path.with_name("stale_artifact.json")
        integrity = None
        if args.skip_existing and contamination_path.exists():
            record(
                {
                    "question_id": question_id,
                    "status": "contaminated",
                    "returncode": None,
                    "answer_path": str(paths.answer_path),
                    "event_log_path": str(paths.event_log_path),
                    "contamination_path": str(contamination_path),
                    "event_integrity": _loads_json(
                        contamination_path.read_text(encoding="utf-8"),
                        label=str(contamination_path),
                    ),
                    "error": "packet answer previously quarantined",
                }
            )
            continue
        if args.skip_existing and stale_artifact_path.exists():
            record(
                {
                    "question_id": question_id,
                    "status": "stale_artifact",
                    "returncode": None,
                    "answer_path": str(paths.answer_path),
                    "event_log_path": str(paths.event_log_path),
                    "stale_artifact_path": str(stale_artifact_path),
                    "error": "packet answer previously quarantined as stale",
                }
            )
            continue
        if args.skip_existing and paths.answer_path.exists():
            integrity = codex_harness.enforce_packet_event_integrity(
                event_log_path=paths.event_log_path,
                answer_path=paths.answer_path,
            )
            if integrity["contaminated"]:
                record(
                    {
                        "question_id": question_id,
                        "status": "contaminated",
                        "returncode": None,
                        "answer_path": str(paths.answer_path),
                        "event_log_path": str(paths.event_log_path),
                        "contamination_path": str(contamination_path),
                        "event_integrity": integrity,
                        "error": "packet answer quarantined after event-log audit",
                    }
                )
                continue
            if not paths.prompt_path.exists() or paths.prompt_path.read_bytes() != prompt_bytes:
                stale = codex_harness.quarantine_stale_packet_answer(
                    answer_path=paths.answer_path,
                    prompt_path=paths.prompt_path,
                    expected_prompt=prompt,
                    reason="prompt_missing_or_mismatch",
                )
                record(
                    {
                        "question_id": question_id,
                        "status": "stale_artifact",
                        "returncode": None,
                        "answer_path": str(paths.answer_path),
                        "event_log_path": str(paths.event_log_path),
                        "stale_artifact_path": str(stale_artifact_path),
                        "stale_artifact": stale,
                        "event_integrity": integrity,
                        "error": "existing answer does not match exact sealed prompt",
                    }
                )
                continue
            if not codex_harness.answer_matches_schema(paths.answer_path, args.schema):
                stale = codex_harness.quarantine_stale_packet_answer(
                    answer_path=paths.answer_path,
                    prompt_path=paths.prompt_path,
                    expected_prompt=prompt,
                    reason="answer_schema_invalid",
                )
                record(
                    {
                        "question_id": question_id,
                        "status": "stale_artifact",
                        "returncode": None,
                        "answer_path": str(paths.answer_path),
                        "event_log_path": str(paths.event_log_path),
                        "stale_artifact_path": str(stale_artifact_path),
                        "stale_artifact": stale,
                        "event_integrity": integrity,
                        "error": "existing answer does not match output schema",
                    }
                )
                continue
            record(
                {
                    "question_id": question_id,
                    "status": "skipped",
                    "returncode": None,
                    "prompt_sha256": _sha256(prompt_bytes),
                    "answer_path": str(paths.answer_path),
                    "event_log_path": str(paths.event_log_path),
                    "stderr_path": str(paths.stderr_path),
                    "event_integrity": integrity,
                }
            )
            continue

        paths.prompt_path.write_bytes(prompt_bytes)
        with codex_harness.question_working_directory(
            mode="packet", requested_cwd=args.cwd
        ) as command_cwd:
            command = codex_harness.build_codex_command(
                prompt=prompt,
                schema_path=args.schema,
                output_path=paths.answer_path,
                event_log_path=paths.event_log_path,
                cwd=command_cwd,
                codex_bin=args.codex_bin,
                model=args.model,
                profile=args.profile,
                reasoning_effort=args.reasoning_effort,
                sandbox=args.sandbox,
                approval=args.approval,
            )
            _write_json_atomic(
                paths.command_path,
                {
                    "args": command.args,
                    "stdout_path": str(command.stdout_path),
                    "stderr_path": str(command.stderr_path),
                    "isolated_packet_cwd": True,
                    "prompt_protocol_version": PROMPT_PROTOCOL_VERSION,
                    "prompt_sha256": _sha256(prompt_bytes),
                },
            )
            result = codex_harness.run_question(
                command,
                prompt,
                timeout=args.timeout,
                dry_run=args.dry_run,
            )
        if not args.dry_run:
            integrity = codex_harness.enforce_packet_event_integrity(
                event_log_path=paths.event_log_path,
                answer_path=paths.answer_path,
            )
            if integrity["contaminated"]:
                result = {
                    "status": "contaminated",
                    "returncode": result.get("returncode"),
                    "error": "packet answer quarantined after event-log audit",
                }
        if result["status"] not in {"ok", "dry_run"} and paths.answer_path.exists():
            paths.answer_path.replace(paths.answer_path.with_name("answer.failed.json"))
        item: dict[str, Any] = {
            "question_id": question_id,
            "status": result["status"],
            "returncode": result["returncode"],
            "prompt_sha256": _sha256(prompt_bytes),
            "answer_path": str(paths.answer_path),
            "event_log_path": str(paths.event_log_path),
            "stderr_path": str(paths.stderr_path),
        }
        if result.get("error"):
            item["error"] = result["error"]
        if result.get("stderr_integrity"):
            item["stderr_integrity"] = result["stderr_integrity"]
        if integrity is not None:
            item["event_integrity"] = integrity
            if integrity["contaminated"]:
                item["contamination_path"] = str(contamination_path)
        record(item)

    cumulative_summary = [summary_by_id[key] for key in sorted(summary_by_id)]
    _write_json_atomic(
        summary_path,
        {"manifest": manifest, "questions": cumulative_summary},
    )
    answered = sum(
        codex_harness.question_result_succeeded(
            item, dry_run=args.dry_run, mode="packet"
        )
        for item in current_summary
    )
    failed = len(current_summary) - answered
    print(
        json.dumps(
            {
                "out_dir": str(args.out_dir),
                "questions": len(current_summary),
                "cumulative_questions": len(cumulative_summary),
                "answered_or_skipped": answered,
                "failed": failed,
                "dry_run": args.dry_run,
            },
            indent=2,
        )
    )
    if not args.dry_run and failed:
        print(
            f"ERROR: {failed} of {len(current_summary)} questions failed "
            "completion/integrity validation",
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
