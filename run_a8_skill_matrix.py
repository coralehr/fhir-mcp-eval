#!/usr/bin/env python3
"""Run the A8 skills-only falsification matrix.

A8 keeps the packet bytes fixed and varies only the prompt condition:

  A8-F0  base prompt
  A8-FL  neutral length-matched pad
  A8-FP  placebo retrieval/process prompt
  A8-FS  FHIR retrieval skill

The runner delegates each cell to codex_harness.py so the per-question prompt,
command, event log, answer, and Codex version manifest stay identical to the
single-arm substrate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_SKILL = Path("eval_skills/fhir_retrieval_playbook.md")

NEUTRAL_CONTROL_TEXT = """# Neutral Length Control

This text is a length-matched control for prompt-size effects. It does not add
domain rules, retrieval tactics, schema knowledge, date handling guidance, or
clinical interpretation advice.

Use the task instructions already supplied by the benchmark. Keep the response
concise, structured, and faithful to the evidence provided in the prompt. Avoid
adding assumptions. If the evidence is incomplete, say that the evidence is
incomplete.
"""

PLACEBO_CONTROL_TEXT = """# Placebo Work Routine

Before answering, organize the task into a short checklist: identify the stated
question, review the available material, avoid repeating work, and provide a
direct answer. Prefer simple wording over elaborate prose.

This routine is intentionally generic. It is meant to control for the presence
of a helpful-looking playbook without teaching domain-specific retrieval,
schema, date-window, reference-resolution, or terminology behavior.
"""


@dataclass(frozen=True)
class SkillArm:
    arm_id: str
    slug: str
    label: str
    skill_file: Path | None
    description: str


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_repo_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def length_match(text: str, target_len: int) -> str:
    seed = text.strip() + "\n"
    if target_len <= 0:
        return ""
    if len(seed) >= target_len:
        return seed[:target_len].rstrip() + "\n"
    chunks: list[str] = []
    while len("\n".join(chunks)) < target_len:
        chunks.append(seed)
    return "\n".join(chunks)[:target_len].rstrip() + "\n"


def write_control_skill_files(control_dir: Path, fhir_skill_file: Path) -> dict[str, Path]:
    target_len = len(fhir_skill_file.read_text(encoding="utf-8"))
    control_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "neutral_length": control_dir / "neutral_length_pad.md",
        "placebo": control_dir / "placebo_work_routine.md",
    }
    files["neutral_length"].write_text(length_match(NEUTRAL_CONTROL_TEXT, target_len), encoding="utf-8")
    files["placebo"].write_text(length_match(PLACEBO_CONTROL_TEXT, target_len), encoding="utf-8")
    return files


def build_arms(*, fhir_skill_file: Path, control_files: dict[str, Path]) -> list[SkillArm]:
    return [
        SkillArm("A8-F0", "base", "Base prompt only", None, "No extra skill text."),
        SkillArm(
            "A8-FL",
            "neutral_length",
            "Neutral length-matched pad",
            control_files["neutral_length"],
            "Controls for prompt length without task guidance.",
        ),
        SkillArm(
            "A8-FP",
            "placebo",
            "Placebo work routine",
            control_files["placebo"],
            "Controls for generic helpful process text.",
        ),
        SkillArm(
            "A8-FS",
            "fhir_skill",
            "FHIR retrieval skill",
            fhir_skill_file,
            "The actual FHIR retrieval playbook under test.",
        ),
    ]


def build_codex_harness_command(
    arm: SkillArm,
    *,
    input_path: Path,
    packet_json: Path,
    out_dir: Path,
    schema: Path,
    cwd: Path,
    model: str | None,
    profile: str | None,
    substrate: str,
    codex_bin: str,
    sandbox: str,
    approval: str,
    limit: int | None,
    timeout: int,
    dry_run: bool,
    live: bool,
    allow_full_run: bool,
    allow_public_artifact: bool,
    skip_existing: bool,
) -> list[str]:
    cmd = [
        sys.executable,
        str(REPO_ROOT / "codex_harness.py"),
        "--mode",
        "packet",
        "--input",
        str(input_path),
        "--packet-json",
        str(packet_json),
        "--out-dir",
        str(out_dir / arm.slug),
        "--schema",
        str(schema),
        "--substrate",
        substrate,
        "--codex-bin",
        codex_bin,
        "--sandbox",
        sandbox,
        "--approval",
        approval,
        "--cwd",
        str(cwd),
        "--timeout",
        str(timeout),
    ]
    if arm.skill_file:
        cmd.extend(["--skill-file", str(arm.skill_file)])
    if model:
        cmd.extend(["--model", model])
    if profile:
        cmd.extend(["--profile", profile])
    if limit is not None:
        cmd.extend(["--limit", str(limit)])
    if dry_run:
        cmd.append("--dry-run")
    if live:
        cmd.append("--live")
    if allow_full_run:
        cmd.append("--allow-full-run")
    if allow_public_artifact:
        cmd.append("--allow-public-artifact")
    if skip_existing:
        cmd.append("--skip-existing")
    return cmd


def file_entry(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    return {"path": str(path), "sha256": sha256_file(path)} if path.exists() else {"path": str(path), "missing": True}


def write_matrix_manifest(
    path: Path,
    *,
    args: argparse.Namespace,
    arms: list[SkillArm],
    control_files: dict[str, Path],
    commands: dict[str, list[str]],
    results: list[dict[str, Any]],
) -> None:
    manifest = {
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "kind": "a8_skill_matrix_manifest",
        "claim_test": "packet bytes fixed; only prompt skill condition varies",
        "config": {
            "input": str(args.input),
            "packet_json": str(args.packet_json),
            "out_dir": str(args.out_dir),
            "model": args.model,
            "profile": args.profile,
            "substrate": args.substrate,
            "limit": args.limit,
            "dry_run": args.dry_run,
        },
        "files": {
            "input": file_entry(args.input),
            "packet_json": file_entry(args.packet_json),
            "fhir_skill": file_entry(args.fhir_skill_file),
            "neutral_length": file_entry(control_files["neutral_length"]),
            "placebo": file_entry(control_files["placebo"]),
        },
        "arms": [asdict(arm) | {"skill_file": str(arm.skill_file) if arm.skill_file else None} for arm in arms],
        "commands": commands,
        "results": results,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def run_command(cmd: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=False, text=True)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run A8 skills-only Codex matrix.")
    parser.add_argument("--input", type=Path, default=Path("final_dataset/full_test409.csv"))
    parser.add_argument("--packet-json", type=Path, default=Path("runs/a7_bonfire_packets.jsonl"))
    parser.add_argument("--out-dir", type=Path, default=Path("runs/a8_skill_matrix"))
    parser.add_argument("--schema", type=Path, default=Path("schemas/codex_answer.schema.json"))
    parser.add_argument("--fhir-skill-file", type=Path, default=DEFAULT_SKILL)
    parser.add_argument("--model", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--substrate", default="codex_subscription")
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_BIN", "codex"))
    parser.add_argument("--sandbox", default="read-only")
    parser.add_argument("--approval", default="never")
    parser.add_argument("--cwd", type=Path, default=REPO_ROOT)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--live", action="store_true", help="acknowledge that this matrix calls Codex and spends quota/time")
    parser.add_argument("--allow-full-run", action="store_true", help="allow a live 4-arm run without --limit")
    parser.add_argument("--allow-public-artifact", action="store_true", help="allow raw prompt/event outputs outside gitignored runs/")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    if not args.dry_run and not args.live:
        raise SystemExit("live A8 runs require --live; keep --dry-run for prompt/manifest generation only")
    if not args.dry_run and not args.allow_full_run and args.limit is None:
        raise SystemExit("unbounded live A8 runs require --allow-full-run or --limit")

    args.input = resolve_repo_path(args.input)
    args.packet_json = resolve_repo_path(args.packet_json)
    args.out_dir = resolve_repo_path(args.out_dir)
    args.schema = resolve_repo_path(args.schema)
    args.fhir_skill_file = resolve_repo_path(args.fhir_skill_file)
    args.cwd = resolve_repo_path(args.cwd)

    control_files = write_control_skill_files(args.out_dir / "generated_controls", args.fhir_skill_file)
    arms = build_arms(fhir_skill_file=args.fhir_skill_file, control_files=control_files)
    commands: dict[str, list[str]] = {}
    results: list[dict[str, Any]] = []
    overall = 0
    for arm in arms:
        arm_out = args.out_dir / arm.slug
        cmd = build_codex_harness_command(
            arm,
            input_path=args.input,
            packet_json=args.packet_json,
            out_dir=args.out_dir,
            schema=args.schema,
            cwd=args.cwd,
            model=args.model,
            profile=args.profile,
            substrate=args.substrate,
            codex_bin=args.codex_bin,
            sandbox=args.sandbox,
            approval=args.approval,
            limit=args.limit,
            timeout=args.timeout,
            dry_run=args.dry_run,
            live=args.live,
            allow_full_run=args.allow_full_run,
            allow_public_artifact=args.allow_public_artifact,
            skip_existing=args.skip_existing,
        )
        commands[arm.arm_id] = cmd
        rc = run_command(cmd, arm_out / "matrix_runner.log")
        status = "ok" if rc == 0 else "error"
        results.append({"arm_id": arm.arm_id, "slug": arm.slug, "returncode": rc, "status": status})
        if rc != 0:
            overall = rc

    write_matrix_manifest(
        args.out_dir / "matrix_manifest.json",
        args=args,
        arms=arms,
        control_files=control_files,
        commands=commands,
        results=results,
    )
    print(json.dumps({"out_dir": str(args.out_dir), "arms": len(arms), "dry_run": args.dry_run, "status": overall}, indent=2))
    return overall


if __name__ == "__main__":
    raise SystemExit(main())
