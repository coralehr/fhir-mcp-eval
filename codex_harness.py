#!/usr/bin/env python3
"""Codex CLI substrate for A6-A9 eval arms.

This runner treats Codex itself as the agent runtime under test. It can run
frozen-packet questions (A6/A7/A8) or live MCP questions (A9) while recording the
Codex CLI version, prompt/schema hashes, event logs, and final structured answer.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


GOLD_FIELD_NAMES = {
    "answer",
    "expected_answer",
    "gold",
    "gold_answer",
    "label",
    "proc_query",
    "sql_query",
    "true_answer",
    "true_fhir_ids",
}

# These fields are useful for artifact provenance but are not clinical
# evidence. Keeping them out of the rendered packet prevents a no-op treatment
# from revealing its arm or producing a different prompt solely because its
# packet hash was recomputed.
MODEL_HIDDEN_PACKET_FIELDS = {
    "bounds",
    "features",
    "kind",
    "plan_only",
    "planner",
    "pinned_reference_targets",
    "resource_count",
    "root_fetch_receipt",
    "sha256",
    "source_queries",
    "source_resource_ids",
}

FORBIDDEN_MODEL_PACKET_KEYS = {
    "expected_answer",
    "gold",
    "gold_answer",
    "proc_query",
    "sql_query",
    "true_answer",
    "true_fhir_ids",
}

# Codex JSONL item types that prove the answering process reached outside the
# frozen packet. Packet-mode answers containing any of these are invalid even
# when Codex ultimately emitted schema-valid JSON.
TOOL_EVENT_TYPES = {
    "code_interpreter_call",
    "command_execution",
    "computer_action",
    "computer_call",
    "dynamic_tool_call",
    "file_change",
    "file_search_call",
    "function_call",
    "local_shell_call",
    "mcp_call",
    "mcp_tool_call",
    "shell_call",
    "tool_call",
    "web_search",
    "web_search_call",
}


@dataclass(frozen=True)
class QuestionPaths:
    prompt_path: Path
    answer_path: Path
    event_log_path: Path
    stderr_path: Path
    command_path: Path


@dataclass(frozen=True)
class CodexCommand:
    args: list[str]
    stdout_path: Path
    stderr_path: Path


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def slugify(value: Any) -> str:
    text = str(value or "unknown").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text.strip("_") or "unknown"


def paths_for_question(out_dir: Path, question_id: Any) -> QuestionPaths:
    qdir = out_dir / "questions" / slugify(question_id)
    qdir.mkdir(parents=True, exist_ok=True)
    return QuestionPaths(
        prompt_path=qdir / "prompt.txt",
        answer_path=qdir / "answer.json",
        event_log_path=qdir / "events.jsonl",
        stderr_path=qdir / "stderr.log",
        command_path=qdir / "command.json",
    )


def terminal_question_status(question_dir: Path) -> str | None:
    """Return the durable terminal outcome recorded for one question.

    ``contamination.json`` takes precedence so a quarantined attempt can never
    become resumable merely because a stray answer file also exists.
    """
    if (question_dir / "contamination.json").exists():
        return "contaminated"
    if (question_dir / "answer.json").exists():
        return "answered"
    return None


def strip_gold_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in GOLD_FIELD_NAMES}


def _json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def model_visible_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Return only clinical evidence and relationship citations for the model.

    Traversal version, bounds, missing/capped edges, and aggregate counts are
    evaluation bookkeeping. A fetched/already-present edge is retained as a
    compact path citation because that relationship is part of the treatment.
    """
    def reject_forbidden(value: Any, path: str = "packet") -> None:
        if isinstance(value, dict):
            forbidden = FORBIDDEN_MODEL_PACKET_KEYS & set(value)
            if forbidden:
                raise ValueError(
                    f"forbidden benchmark key in model packet at {path}: "
                    + ",".join(sorted(forbidden))
                )
            for child_key, child in value.items():
                reject_forbidden(child, f"{path}.{child_key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                reject_forbidden(child, f"{path}[{index}]")

    reject_forbidden(packet)
    visible: dict[str, Any] = {}
    for key, value in packet.items():
        if key in MODEL_HIDDEN_PACKET_FIELDS:
            continue
        if key == "aggregate_summary" and value is None:
            continue
        if key == "reference_traversal":
            traversal = value if isinstance(value, dict) else {}
            receipts = traversal.get("path_receipts")
            citations = []
            if isinstance(receipts, list):
                for receipt in receipts:
                    if not isinstance(receipt, dict) or receipt.get("status") not in {
                        "fetched",
                        "already_present",
                    }:
                        continue
                    citations.append(
                        {
                            field: receipt.get(field)
                            for field in ("depth", "from", "path", "to")
                        }
                    )
            if citations:
                visible[key] = {"path_citations": citations}
            continue
        visible[key] = value
    return visible


def render_model_visible_packet(packet: dict[str, Any]) -> str:
    """Render the exact packet JSON inserted into a packet-mode prompt."""
    return _json_block(model_visible_packet(packet))


def build_prompt(
    row: dict[str, Any],
    *,
    mode: str,
    skill_text: str = "",
    mcp_server_name: str = "bonfire-eval",
    extra_instruction: str = "",
) -> str:
    safe_row = strip_gold_fields(row)
    question = safe_row.get("question") or safe_row.get("question_with_context") or ""
    packet = safe_row.get("packet")
    if packet is None and "context_packet" in safe_row:
        packet = safe_row["context_packet"]
    if isinstance(packet, str):
        try:
            packet = json.loads(packet)
        except json.JSONDecodeError:
            pass

    lines = [
        "You are running a reproducible FHIR-AgentBench evaluation as the answering agent.",
        "Return only the JSON object required by the provided output schema.",
        "Do not use hidden knowledge, training-memory facts, or guesses. Answer only from the supplied packet or configured tools.",
        "If the data is insufficient, set answer to an explicit insufficiency statement and explain the missing evidence.",
        "",
        f"Mode: {mode}",
        f"Question ID: {safe_row.get('question_id', '')}",
        f"Patient FHIR ID: {safe_row.get('patient_fhir_id', '')}",
        "",
        "Question:",
        str(question),
    ]

    # The benchmark's per-question assumption carries the reference "now"
    # (MIMIC dates are future-shifted; relative-date questions are
    # unanswerable without it) and sometimes retrieval hints. Omitting it was
    # the run-1 harness defect documented in docs/A6A_ARTIFACT_REVIEW.md.
    assumption = str(safe_row.get("assumption") or "").strip()
    if assumption and assumption.lower() != "nan":
        lines.extend(["", "Assumption (authoritative for any relative dates):", assumption])

    if skill_text.strip():
        lines.extend(["", "Skill / task playbook:", skill_text.strip()])

    if extra_instruction.strip():
        lines.extend(["", "Additional run instruction:", extra_instruction.strip()])

    if mode == "packet":
        rendered_packet = (
            render_model_visible_packet(packet)
            if isinstance(packet, dict)
            else _json_block(packet if packet is not None else safe_row)
        )
        lines.extend(
            [
                "",
                "Frozen clinical packet:",
                rendered_packet,
                "",
                "Use this packet as read-only evidence. Do not request external data.",
                "Do not call tools, execute commands, inspect the filesystem, or read files; any such event invalidates the answer.",
            ]
        )
    elif mode == "mcp":
        lines.extend(
            [
                "",
                f"Use the configured Codex MCP server named '{mcp_server_name}' if you need clinical data.",
                "Use only tools relevant to this patient/question. Avoid repeated identical calls.",
                "Cite source FHIR resource IDs in source_resource_ids.",
            ]
        )
    else:
        raise ValueError(f"Unknown mode: {mode}")

    return "\n".join(lines).strip() + "\n"


def build_codex_command(
    *,
    prompt: str,
    schema_path: Path,
    output_path: Path,
    event_log_path: Path,
    cwd: Path,
    codex_bin: str = "codex",
    model: str | None = None,
    profile: str | None = None,
    reasoning_effort: str | None = None,
    sandbox: str = "read-only",
    approval: str = "never",
) -> CodexCommand:
    # Codex changes directory before loading the schema and writing the final
    # message. Resolve host-side paths so packet-mode isolation cannot break
    # either artifact.
    schema_path = schema_path.resolve()
    output_path = output_path.resolve()
    event_log_path = event_log_path.resolve()
    cwd = cwd.resolve()
    args = [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--json",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "-C",
        str(cwd),
        "-s",
        sandbox,
    ]
    if reasoning_effort:
        args += ["-c", f'model_reasoning_effort="{reasoning_effort}"']
    if model:
        args.extend(["-m", model])
    if profile:
        args.extend(["-p", profile])
    args.append("-")
    return CodexCommand(
        args=args,
        stdout_path=event_log_path,
        stderr_path=event_log_path.with_name("stderr.log"),
    )


@contextlib.contextmanager
def question_working_directory(*, mode: str, requested_cwd: Path) -> Iterator[Path]:
    """Yield an empty, non-repository cwd for frozen-packet answering.

    MCP mode keeps its configured cwd because its tool server may deliberately
    depend on repository configuration. Packet mode has no such dependency and
    must not begin in a directory containing benchmark answers or prior runs.
    """
    if mode != "packet":
        yield requested_cwd.resolve()
        return
    with tempfile.TemporaryDirectory(prefix="fhir-agentbench-packet-") as tmp:
        yield Path(tmp).resolve()


def _normalized_event_type(value: Any) -> str:
    return re.sub(r"[.:-]+", "_", str(value or "").strip().lower())


def _is_tool_event_type(value: Any) -> bool:
    event_type = _normalized_event_type(value)
    if event_type in TOOL_EVENT_TYPES:
        return True
    return (
        ("command" in event_type and "execution" in event_type)
        or ("tool" in event_type and "call" in event_type)
        or ("shell" in event_type and "call" in event_type)
        or event_type.startswith("mcp_")
    )


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one JSON object while rejecting duplicate decoded keys."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def audit_event_log(event_log_path: Path) -> dict[str, Any]:
    """Audit a Codex JSONL log for packet-contaminating tool events.

    The returned details intentionally contain event types and line numbers,
    never command text or tool output, so the audit receipt cannot duplicate
    benchmark data read by a contaminated process.
    """
    findings: list[dict[str, Any]] = []
    parse_errors: list[int] = []
    integrity_errors: list[str] = []
    event_count = 0
    turn_completed_count = 0
    turn_failed_count = 0
    error_event_count = 0
    thread_started_count = 0
    turn_started_count = 0
    item_event_count = 0
    event_type_sequence: list[str] = []
    parsed_events: list[dict[str, Any]] = []
    if not event_log_path.exists():
        return {
            "contaminated": True,
            "event_log_exists": False,
            "findings": findings,
            "parse_error_lines": parse_errors,
            "integrity_errors": ["event_log_missing"],
            "event_count": 0,
            "turn_completed_count": 0,
            "turn_failed_count": 0,
            "error_event_count": 0,
            "thread_started_count": 0,
            "turn_started_count": 0,
            "item_event_count": 0,
            "event_type_sequence": [],
            "utf8_valid": False,
            "terminal_newline": False,
            "provider_failure_shape": False,
        }
    payload = event_log_path.read_bytes()
    utf8_valid = True
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        text = ""
        utf8_valid = False
        integrity_errors.append("event_log_invalid_utf8")
    terminal_newline = payload.endswith(b"\n")
    if payload and not terminal_newline:
        integrity_errors.append("event_log_missing_terminal_newline")
    for line_number, line in enumerate(text.splitlines(), start=1):
        try:
            event = json.loads(line, object_pairs_hook=_unique_json_object)
        except Exception:
            parse_errors.append(line_number)
            continue
        if not isinstance(event, dict):
            integrity_errors.append(f"event_not_object:{line_number}")
            continue
        parsed_events.append(event)
        event_count += 1
        event_type = _normalized_event_type(event.get("type"))
        event_type_sequence.append(event_type)
        if event_type == "turn_completed":
            turn_completed_count += 1
        elif event_type == "turn_failed":
            turn_failed_count += 1
        elif event_type == "error":
            error_event_count += 1
        elif event_type == "thread_started":
            thread_started_count += 1
        elif event_type == "turn_started":
            turn_started_count += 1
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = _normalized_event_type(item.get("type"))
        if item or event_type in {"item_started", "item_completed"}:
            item_event_count += 1
        if _is_tool_event_type(event_type) or _is_tool_event_type(item_type):
            findings.append(
                {
                    "line": line_number,
                    "event_type": event_type or None,
                    "item_type": item_type or None,
                    "item_id": str(item.get("id")) if item.get("id") is not None else None,
                }
            )
    if event_count == 0:
        integrity_errors.append("event_log_empty")
    if turn_completed_count == 0:
        integrity_errors.append("turn_completed_missing")
    provider_failure_shape = False
    if len(parsed_events) == 4:
        thread_started, turn_started, error_event, turn_failed = parsed_events
        nested_error = turn_failed.get("error")
        error_message = error_event.get("message")
        provider_failure_shape = (
            set(thread_started) == {"type", "thread_id"}
            and thread_started.get("type") == "thread.started"
            and isinstance(thread_started.get("thread_id"), str)
            and bool(thread_started["thread_id"])
            and set(turn_started) == {"type"}
            and turn_started.get("type") == "turn.started"
            and set(error_event) == {"type", "message"}
            and error_event.get("type") == "error"
            and isinstance(error_message, str)
            and bool(error_message)
            and set(turn_failed) == {"type", "error"}
            and turn_failed.get("type") == "turn.failed"
            and isinstance(nested_error, dict)
            and set(nested_error) == {"message"}
            and nested_error.get("message") == error_message
        )
    return {
        # A malformed, empty, or unterminated stream makes the audit
        # unverifiable. Fail closed rather than accepting an answer whose tool
        # history may have been truncated or deleted.
        "contaminated": bool(findings or parse_errors or integrity_errors),
        "event_log_exists": True,
        "findings": findings,
        "parse_error_lines": parse_errors,
        "integrity_errors": integrity_errors,
        "event_count": event_count,
        "turn_completed_count": turn_completed_count,
        "turn_failed_count": turn_failed_count,
        "error_event_count": error_event_count,
        "thread_started_count": thread_started_count,
        "turn_started_count": turn_started_count,
        "item_event_count": item_event_count,
        "event_type_sequence": event_type_sequence,
        "utf8_valid": utf8_valid,
        "terminal_newline": terminal_newline,
        "provider_failure_shape": provider_failure_shape,
    }


def is_retryable_incomplete_packet_audit(audit: object) -> bool:
    """Identify a well-formed provider failure that produced no answer turn.

    ``audit_event_log`` intentionally marks every incomplete stream as
    contaminated so no answer can be accepted from it. A controller may retry
    only this narrower shape: the CLI explicitly emitted both an error and a
    failed turn, the log parsed completely, and no tool event was observed.
    """

    return (
        isinstance(audit, dict)
        and audit.get("contaminated") is True
        and audit.get("event_log_exists") is True
        and audit.get("findings") == []
        and audit.get("parse_error_lines") == []
        and audit.get("integrity_errors") == ["turn_completed_missing"]
        and audit.get("turn_completed_count") == 0
        and audit.get("event_count") == 4
        and audit.get("thread_started_count") == 1
        and audit.get("turn_started_count") == 1
        and audit.get("error_event_count") == 1
        and audit.get("turn_failed_count") == 1
        and audit.get("item_event_count") == 0
        and audit.get("event_type_sequence")
        == ["thread_started", "turn_started", "error", "turn_failed"]
        and audit.get("utf8_valid") is True
        and audit.get("terminal_newline") is True
        and audit.get("provider_failure_shape") is True
    )


def retryable_incomplete_packet_marker_matches(
    marker: object,
    audit: object,
) -> bool:
    """Require the durable marker to be the exact retryable audit receipt."""

    return (
        is_retryable_incomplete_packet_audit(audit)
        and isinstance(audit, dict)
        and marker == {**audit, "quarantine_path": None}
    )


def enforce_packet_event_integrity(*, event_log_path: Path, answer_path: Path) -> dict[str, Any]:
    """Quarantine a packet answer when its event log contains any tool use."""
    audit = audit_event_log(event_log_path)
    quarantine_path: Path | None = None
    if audit["contaminated"] and answer_path.exists():
        quarantine_path = answer_path.with_name("answer.contaminated.json")
        answer_path.replace(quarantine_path)
    receipt = {
        **audit,
        "quarantine_path": str(quarantine_path) if quarantine_path else None,
    }
    if audit["contaminated"]:
        receipt_path = answer_path.with_name("contamination.json")
        receipt_path.write_text(_json_block(receipt) + "\n", encoding="utf-8")
    return receipt


def _matches_json_schema(value: Any, schema: dict[str, Any]) -> bool:
    """Validate the small JSON-Schema subset used by Codex answer schemas."""
    declared_type = schema.get("type")
    if isinstance(declared_type, list):
        return any(_matches_json_schema(value, {**schema, "type": item}) for item in declared_type)
    if declared_type == "null":
        return value is None
    if declared_type == "string":
        return isinstance(value, str)
    if declared_type == "array":
        if not isinstance(value, list):
            return False
        item_schema = schema.get("items")
        return not isinstance(item_schema, dict) or all(
            _matches_json_schema(item, item_schema) for item in value
        )
    if declared_type == "object":
        if not isinstance(value, dict):
            return False
        required = schema.get("required", [])
        if not isinstance(required, list) or any(key not in value for key in required):
            return False
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return False
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return False
        return all(
            key not in value
            or not isinstance(child_schema, dict)
            or _matches_json_schema(value[key], child_schema)
            for key, child_schema in properties.items()
        )
    return True


def answer_matches_schema(answer_path: Path, schema_path: Path) -> bool:
    """Return whether an existing answer is parseable and matches its output schema."""
    try:
        answer = json.loads(answer_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(schema, dict) and _matches_json_schema(answer, schema)


def quarantine_stale_packet_answer(
    *,
    answer_path: Path,
    prompt_path: Path,
    expected_prompt: str,
    reason: str,
) -> dict[str, Any]:
    """Quarantine an answer whose rendered prompt/schema binding is stale."""
    marker_path = answer_path.with_name("stale_artifact.json")
    quarantine_path = answer_path.with_name("answer.stale.json")
    if quarantine_path.exists():
        raise ValueError(f"stale answer quarantine already exists: {quarantine_path}")
    if answer_path.exists():
        answer_path.replace(quarantine_path)
    actual_prompt_sha256 = (
        sha256_file(prompt_path) if prompt_path.exists() else None
    )
    marker = {
        "kind": "stale_packet_answer",
        "reason": reason,
        "expected_prompt_sha256": sha256_text(expected_prompt),
        "actual_prompt_sha256": actual_prompt_sha256,
        "quarantine_path": str(quarantine_path) if quarantine_path.exists() else None,
    }
    _write_json_atomic(marker_path, marker)
    return marker


def load_rows(input_path: Path, limit: int | None = None, question_ids: set[str] | None = None) -> list[dict[str, Any]]:
    with input_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if question_ids:
        rows = [r for r in rows if str(r.get("question_id")) in question_ids]
    if limit is not None:
        rows = rows[:limit]
    return rows


def load_packets(packet_json: Path | None) -> dict[str, Any]:
    if not packet_json:
        return {}
    text = packet_json.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    if packet_json.suffix == ".jsonl":
        packets = {}
        for line in text.splitlines():
            item = json.loads(line)
            packets[str(item["question_id"])] = item
        return packets
    data = json.loads(text)
    if isinstance(data, list):
        return {str(item["question_id"]): item for item in data}
    if isinstance(data, dict):
        if all(isinstance(v, dict) for v in data.values()):
            return {str(k): v for k, v in data.items()}
        if "question_id" in data:
            return {str(data["question_id"]): data}
    raise ValueError(f"Unsupported packet JSON shape: {packet_json}")


def validate_packet_coverage(*, mode: str, rows: list[dict[str, Any]], packets: dict[str, Any], packet_json: Path | None) -> None:
    if mode != "packet":
        return
    if packet_json is None:
        raise SystemExit("packet mode requires --packet-json so benchmark SQL/procedure metadata is never used as evidence")
    missing = [str(row.get("question_id")) for row in rows if str(row.get("question_id")) not in packets]
    if missing:
        preview = ", ".join(missing[:10])
        suffix = "..." if len(missing) > 10 else ""
        raise SystemExit(f"packet JSON is missing {len(missing)} requested question_id(s): {preview}{suffix}")


def validate_out_dir(out_dir: Path, *, allow_public_artifact: bool) -> None:
    repo = Path(__file__).resolve().parent
    resolved = out_dir.resolve()
    try:
        rel = resolved.relative_to(repo)
    except ValueError:
        return
    if rel.parts and rel.parts[0] == "runs":
        return
    if allow_public_artifact:
        return
    raise SystemExit(
        "Codex run outputs include raw prompts/events. Use an ignored out-dir under runs/, "
        "or pass --allow-public-artifact after a de-id/license review."
    )


def run_version(codex_bin: str) -> str:
    try:
        proc = subprocess.run([codex_bin, "--version"], check=False, text=True, capture_output=True, timeout=20)
        return (proc.stdout or proc.stderr).strip()
    except Exception as exc:
        return f"unavailable: {exc}"


def git_commit_and_dirty(repo: Path) -> tuple[str, bool]:
    try:
        commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        status = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True)
        return commit, bool(status.strip())
    except Exception:
        return "unknown", True


def write_manifest(
    *,
    manifest_path: Path,
    run_config: dict[str, Any],
    files: dict[str, Path | None],
    codex_version: str,
    git_commit: str,
    git_dirty: bool,
) -> dict[str, Any]:
    file_entries = {}
    for name, path in files.items():
        if not path:
            continue
        file_entries[name] = {
            "path": str(path),
            "sha256": sha256_file(path),
        }
    identity = {
        "substrate": "codex",
        "codex_version": codex_version,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "git": {"commit": git_commit, "dirty": git_dirty},
        "run_config": run_config,
        "files": file_entries,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            raise ValueError("immutable harness manifest is not a JSON object")
        existing_identity = {
            key: value for key, value in existing.items() if key != "created_at"
        }
        if existing_identity != identity:
            raise ValueError(
                "immutable harness manifest does not match this resume configuration"
            )
        return existing
    manifest = {
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        **identity,
    }
    _write_json_atomic(manifest_path, manifest)
    return manifest


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(_json_block(value) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load_summary_items(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("questions"), list):
        raise ValueError("existing summary.json has an invalid shape")
    return {
        str(item["question_id"]): item
        for item in value["questions"]
        if isinstance(item, dict) and item.get("question_id") is not None
    }


def question_result_succeeded(
    item: dict[str, Any], *, dry_run: bool, mode: str = "packet"
) -> bool:
    """Return true only for a complete, audited result from this invocation."""
    if dry_run:
        return item.get("status") == "dry_run"
    if item.get("status") not in {"ok", "skipped"}:
        return False
    if item.get("status") == "ok" and item.get("returncode") != 0:
        return False
    if mode == "packet":
        integrity = item.get("event_integrity")
        if (
            not isinstance(integrity, dict)
            or integrity.get("contaminated") is not False
        ):
            return False
    answer_path = item.get("answer_path")
    return bool(answer_path and Path(str(answer_path)).is_file())


def audit_stderr(stderr_path: Path) -> dict[str, Any]:
    """Return content-free transport metadata for the separated stderr stream."""

    if not stderr_path.exists():
        return {
            "exists": False,
            "empty": False,
            "byte_count": 0,
            "sha256": None,
            "utf8_valid": False,
            "terminal_newline": False,
        }
    payload = stderr_path.read_bytes()
    try:
        payload.decode("utf-8")
        utf8_valid = True
    except UnicodeDecodeError:
        utf8_valid = False
    return {
        "exists": True,
        "empty": not payload,
        "byte_count": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "utf8_valid": utf8_valid,
        "terminal_newline": not payload or payload.endswith(b"\n"),
    }


def run_question(command: CodexCommand, prompt: str, *, timeout: int, dry_run: bool) -> dict[str, Any]:
    command.stdout_path.parent.mkdir(parents=True, exist_ok=True)
    command.stderr_path.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        return {"status": "dry_run", "returncode": None}
    try:
        with (
            command.stdout_path.open("w", encoding="utf-8") as stdout,
            command.stderr_path.open("w", encoding="utf-8") as stderr,
        ):
            proc = subprocess.run(
                command.args,
                input=prompt,
                text=True,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout,
                check=False,
            )
        stderr_integrity = audit_stderr(command.stderr_path)
        stderr_clean = stderr_integrity["empty"] is True
        return {
            "status": "ok" if proc.returncode == 0 and stderr_clean else "error",
            "returncode": proc.returncode,
            "stderr_integrity": stderr_integrity,
            **(
                {}
                if stderr_clean
                else {"error": "nonempty Codex stderr rejected by sealed transport"}
            ),
        }
    except subprocess.TimeoutExpired:
        with command.stderr_path.open("a", encoding="utf-8") as stderr:
            stderr.write(
                json.dumps({"runner_error": "timeout", "timeout_seconds": timeout})
                + "\n"
            )
        return {
            "status": "timeout",
            "returncode": None,
            "error": f"timeout after {timeout}s",
            "stderr_integrity": audit_stderr(command.stderr_path),
        }
    except OSError as exc:
        with command.stderr_path.open("a", encoding="utf-8") as stderr:
            stderr.write(json.dumps({"runner_error": "os_error"}) + "\n")
        return {
            "status": "error",
            "returncode": None,
            "error": str(exc),
            "stderr_integrity": audit_stderr(command.stderr_path),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FHIR eval questions through Codex exec.")
    parser.add_argument("--mode", choices=["packet", "mcp"], required=True)
    parser.add_argument("--input", type=Path, default=Path("final_dataset/full_test409.csv"))
    parser.add_argument("--packet-json", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=Path("runs/codex"))
    parser.add_argument("--schema", type=Path, default=Path("schemas/codex_answer.schema.json"))
    parser.add_argument("--skill-file", type=Path, default=None)
    parser.add_argument("--extra-instruction", default="")
    parser.add_argument("--mcp-server-name", default="bonfire-eval")
    parser.add_argument("--model", default=None)
    parser.add_argument("--reasoning-effort", default=None, choices=["low", "medium", "high", "xhigh"], help="pin model_reasoning_effort; otherwise the machine config default leaks in (see run-2 model-mix disclosure)")
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
    parser.add_argument("--live", action="store_true", help="acknowledge that this will call Codex and spend quota/time")
    parser.add_argument("--allow-full-run", action="store_true", help="allow live runs without --limit or --question-id")
    parser.add_argument("--allow-public-artifact", action="store_true", help="allow raw prompt/event outputs outside gitignored runs/")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parent
    if not args.dry_run and not args.live:
        raise SystemExit("live Codex runs require --live; use --dry-run for prompt/manifest generation only")
    if not args.dry_run and not args.allow_full_run and args.limit is None and not args.question_id:
        raise SystemExit("unbounded live Codex runs require --allow-full-run, or provide --limit/--question-id")
    validate_out_dir(args.out_dir, allow_public_artifact=args.allow_public_artifact)

    rows = load_rows(args.input, limit=args.limit, question_ids=set(args.question_id) if args.question_id else None)
    packets = load_packets(args.packet_json)
    validate_packet_coverage(mode=args.mode, rows=rows, packets=packets, packet_json=args.packet_json)
    skill_text = args.skill_file.read_text(encoding="utf-8") if args.skill_file else ""
    run_config = {
        "mode": args.mode,
        "substrate": args.substrate,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "profile": args.profile,
        "sandbox": args.sandbox,
        "approval": args.approval,
        "mcp_server_name": args.mcp_server_name if args.mode == "mcp" else None,
        "packet_cwd_isolated": args.mode == "packet",
        "dry_run": args.dry_run,
        "live": args.live,
        "question_count": len(rows),
        "timeout_seconds": args.timeout,
        "extra_instruction": args.extra_instruction,
        "codex_bin": args.codex_bin,
        "ignore_user_config": True,
        "ignore_rules": True,
    }
    git_commit, git_dirty = git_commit_and_dirty(repo)
    manifest = write_manifest(
        manifest_path=args.out_dir / "manifest.json",
        run_config=run_config,
        files={
            "input": args.input,
            "packet_json": args.packet_json,
            "schema": args.schema,
            "skill_file": args.skill_file,
            "harness": Path(__file__).resolve(),
        },
        codex_version=run_version(args.codex_bin),
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
        qid = str(row.get("question_id"))
        packet = packets.get(qid)
        prompt_row = {**row, **(packet or {})}
        prompt = build_prompt(
            prompt_row,
            mode=args.mode,
            skill_text=skill_text,
            mcp_server_name=args.mcp_server_name,
            extra_instruction=args.extra_instruction,
        )
        paths = paths_for_question(args.out_dir, qid)
        contamination_path = paths.answer_path.with_name("contamination.json")
        stale_artifact_path = paths.answer_path.with_name("stale_artifact.json")
        integrity = None
        if args.skip_existing and contamination_path.exists():
            record(
                {
                    "question_id": qid,
                    "status": "contaminated",
                    "returncode": None,
                    "answer_path": str(paths.answer_path),
                    "event_log_path": str(paths.event_log_path),
                    "contamination_path": str(contamination_path),
                    "event_integrity": json.loads(
                        contamination_path.read_text(encoding="utf-8")
                    ),
                    "error": "packet answer previously quarantined after event-log integrity failure",
                }
            )
            continue
        if args.skip_existing and stale_artifact_path.exists():
            record(
                {
                    "question_id": qid,
                    "status": "stale_artifact",
                    "returncode": None,
                    "answer_path": str(paths.answer_path),
                    "event_log_path": str(paths.event_log_path),
                    "stale_artifact_path": str(stale_artifact_path),
                    "error": "packet answer previously quarantined after stale prompt/schema validation",
                }
            )
            continue
        if args.skip_existing and paths.answer_path.exists():
            if args.mode == "packet":
                integrity = enforce_packet_event_integrity(
                    event_log_path=paths.event_log_path,
                    answer_path=paths.answer_path,
                )
                if integrity["contaminated"]:
                    record(
                        {
                            "question_id": qid,
                            "status": "contaminated",
                            "returncode": None,
                            "answer_path": str(paths.answer_path),
                            "event_log_path": str(paths.event_log_path),
                            "contamination_path": str(contamination_path),
                            "event_integrity": integrity,
                            "error": "packet answer quarantined after event-log integrity failure",
                        }
                    )
                    continue
                prompt_matches = (
                    paths.prompt_path.exists()
                    and paths.prompt_path.read_bytes() == prompt.encode("utf-8")
                )
                if not prompt_matches:
                    stale = quarantine_stale_packet_answer(
                        answer_path=paths.answer_path,
                        prompt_path=paths.prompt_path,
                        expected_prompt=prompt,
                        reason="prompt_missing_or_mismatch",
                    )
                    record(
                        {
                            "question_id": qid,
                            "status": "stale_artifact",
                            "returncode": None,
                            "answer_path": str(paths.answer_path),
                            "event_log_path": str(paths.event_log_path),
                            "stale_artifact_path": str(stale_artifact_path),
                            "stale_artifact": stale,
                            "event_integrity": integrity,
                            "error": "existing packet answer prompt does not match freshly rendered prompt",
                        }
                    )
                    continue
                if not answer_matches_schema(paths.answer_path, args.schema):
                    stale = quarantine_stale_packet_answer(
                        answer_path=paths.answer_path,
                        prompt_path=paths.prompt_path,
                        expected_prompt=prompt,
                        reason="answer_schema_invalid",
                    )
                    record(
                        {
                            "question_id": qid,
                            "status": "stale_artifact",
                            "returncode": None,
                            "answer_path": str(paths.answer_path),
                            "event_log_path": str(paths.event_log_path),
                            "stale_artifact_path": str(stale_artifact_path),
                            "stale_artifact": stale,
                            "event_integrity": integrity,
                            "error": "existing packet answer does not match the output schema",
                        }
                    )
                    continue
            record(
                {
                    "question_id": qid,
                    "status": "skipped",
                    "returncode": None,
                    "answer_path": str(paths.answer_path),
                    "event_log_path": str(paths.event_log_path),
                    "event_integrity": integrity,
                }
            )
            continue
        paths.prompt_path.write_text(prompt, encoding="utf-8")
        with question_working_directory(mode=args.mode, requested_cwd=args.cwd) as command_cwd:
            command = build_codex_command(
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
            paths.command_path.write_text(
                _json_block(
                    {
                        "args": command.args,
                        "stdout_path": str(command.stdout_path),
                        "stderr_path": str(command.stderr_path),
                        "isolated_packet_cwd": args.mode == "packet",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = run_question(command, prompt, timeout=args.timeout, dry_run=args.dry_run)
        if args.mode == "packet" and not args.dry_run:
            integrity = enforce_packet_event_integrity(
                event_log_path=paths.event_log_path,
                answer_path=paths.answer_path,
            )
            if integrity["contaminated"]:
                result = {
                    "status": "contaminated",
                    "returncode": result.get("returncode"),
                    "error": "packet answer quarantined after event-log integrity failure",
                }
        if result["status"] not in {"ok", "dry_run"} and paths.answer_path.exists():
            # Never leave a nonzero/timeout attempt looking resumable merely
            # because Codex wrote a partial last message before failing.
            failed_answer_path = paths.answer_path.with_name("answer.failed.json")
            paths.answer_path.replace(failed_answer_path)
        item = {
            "question_id": qid,
            "status": result["status"],
            "returncode": result["returncode"],
            "prompt_sha256": sha256_text(prompt),
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

    cumulative_summary = [summary_by_id[qid] for qid in sorted(summary_by_id)]
    _write_json_atomic(
        summary_path, {"manifest": manifest, "questions": cumulative_summary}
    )
    answered = sum(
        question_result_succeeded(item, dry_run=args.dry_run, mode=args.mode)
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
    # Fail loudly when live questions produced no answer file (e.g. the
    # 2026-07-11 silent 0/50 on a usage-limit error): exit non-zero so callers
    # and logs cannot mistake a dead run for a completed one.
    if not args.dry_run and failed:
        print(
            f"ERROR: {failed} of {len(current_summary)} questions failed completion/integrity validation",
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
