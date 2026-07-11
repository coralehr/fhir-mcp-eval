#!/usr/bin/env python3
"""Codex CLI substrate for A6-A9 eval arms.

This runner treats Codex itself as the agent runtime under test. It can run
frozen-packet questions (A6/A7/A8) or live MCP questions (A9) while recording the
Codex CLI version, prompt/schema hashes, event logs, and final structured answer.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


@dataclass(frozen=True)
class QuestionPaths:
    prompt_path: Path
    answer_path: Path
    event_log_path: Path
    command_path: Path


@dataclass(frozen=True)
class CodexCommand:
    args: list[str]
    stdout_path: Path


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
        command_path=qdir / "command.json",
    )


def strip_gold_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in GOLD_FIELD_NAMES}


def _json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


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

    if skill_text.strip():
        lines.extend(["", "Skill / task playbook:", skill_text.strip()])

    if extra_instruction.strip():
        lines.extend(["", "Additional run instruction:", extra_instruction.strip()])

    if mode == "packet":
        lines.extend(
            [
                "",
                "Frozen clinical packet:",
                _json_block(packet if packet is not None else safe_row),
                "",
                "Use this packet as read-only evidence. Do not request external data.",
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
    sandbox: str = "read-only",
    approval: str = "never",
) -> CodexCommand:
    args = [
        codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
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
    if model:
        args.extend(["-m", model])
    if profile:
        args.extend(["-p", profile])
    args.append("-")
    return CodexCommand(args=args, stdout_path=event_log_path)


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
    manifest = {
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "substrate": "codex",
        "codex_version": codex_version,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "git": {"commit": git_commit, "dirty": git_dirty},
        "run_config": run_config,
        "files": file_entries,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(_json_block(manifest) + "\n", encoding="utf-8")
    return manifest


def run_question(command: CodexCommand, prompt: str, *, timeout: int, dry_run: bool) -> dict[str, Any]:
    command.stdout_path.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        return {"status": "dry_run", "returncode": None}
    try:
        with command.stdout_path.open("w", encoding="utf-8") as stdout:
            proc = subprocess.run(
                command.args,
                input=prompt,
                text=True,
                stdout=stdout,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        return {"status": "ok" if proc.returncode == 0 else "error", "returncode": proc.returncode}
    except subprocess.TimeoutExpired:
        with command.stdout_path.open("a", encoding="utf-8") as stdout:
            stdout.write(json.dumps({"error": "timeout", "timeout_seconds": timeout}) + "\n")
        return {"status": "timeout", "returncode": None, "error": f"timeout after {timeout}s"}
    except OSError as exc:
        with command.stdout_path.open("a", encoding="utf-8") as stdout:
            stdout.write(json.dumps({"error": "os_error", "detail": str(exc)}) + "\n")
        return {"status": "error", "returncode": None, "error": str(exc)}


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
        "profile": args.profile,
        "sandbox": args.sandbox,
        "approval": args.approval,
        "mcp_server_name": args.mcp_server_name if args.mode == "mcp" else None,
        "dry_run": args.dry_run,
        "live": args.live,
        "question_count": len(rows),
    }
    git_commit, git_dirty = git_commit_and_dirty(repo)
    manifest = write_manifest(
        manifest_path=args.out_dir / "manifest.json",
        run_config=run_config,
        files={"input": args.input, "packet_json": args.packet_json, "schema": args.schema, "skill_file": args.skill_file},
        codex_version=run_version(args.codex_bin),
        git_commit=git_commit,
        git_dirty=git_dirty,
    )

    summary = []
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
        if args.skip_existing and paths.answer_path.exists():
            summary.append({"question_id": qid, "status": "skipped"})
            continue
        paths.prompt_path.write_text(prompt, encoding="utf-8")
        command = build_codex_command(
            prompt=prompt,
            schema_path=args.schema,
            output_path=paths.answer_path,
            event_log_path=paths.event_log_path,
            cwd=args.cwd,
            codex_bin=args.codex_bin,
            model=args.model,
            profile=args.profile,
            sandbox=args.sandbox,
            approval=args.approval,
        )
        paths.command_path.write_text(_json_block({"args": command.args, "stdout_path": str(command.stdout_path)}) + "\n", encoding="utf-8")
        result = run_question(command, prompt, timeout=args.timeout, dry_run=args.dry_run)
        item = {
            "question_id": qid,
            "status": result["status"],
            "returncode": result["returncode"],
            "prompt_sha256": sha256_text(prompt),
            "answer_path": str(paths.answer_path),
            "event_log_path": str(paths.event_log_path),
        }
        if result.get("error"):
            item["error"] = result["error"]
        summary.append(item)

    (args.out_dir / "summary.json").write_text(_json_block({"manifest": manifest, "questions": summary}) + "\n", encoding="utf-8")
    print(json.dumps({"out_dir": str(args.out_dir), "questions": len(summary), "dry_run": args.dry_run}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
