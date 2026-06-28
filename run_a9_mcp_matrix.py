#!/usr/bin/env python3
"""Run the A9 Codex + MCP/tools matrix.

A9 tests whether skills compound with an actual tool interface:

  A9-T0   generic MCP
  A9-TSg  generic MCP + FHIR skill
  A9-Tb   Bonfire/read MCP
  A9-Tbs  Bonfire/read MCP + FHIR skill

The treatment server already owns the tool catalog through TOOL_SUBSET. This
runner only selects the subset, starts the server when requested, and delegates
the answering run to codex_harness.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_SKILL = Path("eval_skills/fhir_retrieval_playbook.md")


@dataclass(frozen=True)
class McpArm:
    arm_id: str
    slug: str
    label: str
    tool_subset: str
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


def build_arms(
    *,
    fhir_skill_file: Path,
    generic_subset: str = "control",
    bonfire_subset: str = "arm_full8",
) -> list[McpArm]:
    return [
        McpArm("A9-T0", "generic_mcp", "Generic FHIR MCP", generic_subset, None, "Generic FHIR request tool only."),
        McpArm(
            "A9-TSg",
            "generic_mcp_skill",
            "Generic FHIR MCP + skill",
            generic_subset,
            fhir_skill_file,
            "Generic FHIR request tool with the FHIR retrieval playbook.",
        ),
        McpArm(
            "A9-Tb",
            "expanded_read_mcp",
            "Expanded read-tool MCP proxy",
            bonfire_subset,
            None,
            "Current typed read-tool catalog proxy; not yet a governed Bonfire read-contract tool.",
        ),
        McpArm(
            "A9-Tbs",
            "expanded_read_mcp_skill",
            "Expanded read-tool MCP proxy + skill",
            bonfire_subset,
            fhir_skill_file,
            "Current typed read-tool catalog proxy with the FHIR retrieval playbook.",
        ),
    ]


def build_codex_harness_command(
    arm: McpArm,
    *,
    input_path: Path,
    out_dir: Path,
    schema: Path,
    cwd: Path,
    mcp_server_name: str,
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
        "mcp",
        "--input",
        str(input_path),
        "--out-dir",
        str(out_dir / arm.slug),
        "--schema",
        str(schema),
        "--mcp-server-name",
        mcp_server_name,
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


def safe_child_env(base_env: dict[str, str]) -> dict[str, str]:
    allow = {
        "HOME",
        "LANG",
        "LC_ALL",
        "NO_PROXY",
        "PATH",
        "PYTHONPATH",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "VIRTUAL_ENV",
    }
    return {key: value for key, value in base_env.items() if key in allow}


def build_server_env(
    base_env: dict[str, str],
    *,
    arm: McpArm,
    medplum_base_url: str | None,
    port: int,
) -> dict[str, str]:
    env = safe_child_env(base_env)
    env["TOOL_SUBSET"] = arm.tool_subset
    env["TREATMENT_PORT"] = str(port)
    if medplum_base_url:
        env["MEDPLUM_BASE_URL"] = medplum_base_url
    for key in ("MEDPLUM_EMAIL", "MEDPLUM_PASSWORD"):
        if key in base_env:
            env[key] = base_env[key]
    return env


def configured_mcp_url(codex_bin: str, server_name: str) -> str | None:
    proc = subprocess.run([codex_bin, "mcp", "list", "--json"], text=True, capture_output=True, check=False, timeout=20)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or f"{codex_bin} mcp list failed")
    text = proc.stdout.strip()
    start = text.find("[")
    if start < 0:
        raise RuntimeError("codex mcp list --json did not return a JSON list")
    data = json.loads(text[start:])
    for server in data:
        if server.get("name") != server_name:
            continue
        transport = server.get("transport") or {}
        return transport.get("url")
    return None


def verify_mcp_registration(*, codex_bin: str, server_name: str, expected_url: str) -> None:
    actual_url = configured_mcp_url(codex_bin, server_name)
    if actual_url != expected_url:
        raise SystemExit(
            f"Codex MCP server '{server_name}' is registered as {actual_url!r}, expected {expected_url!r}. "
            f"Run: codex mcp add {server_name} --url {expected_url}"
        )


def wait_for_tcp(host: str, port: int, proc: subprocess.Popen[str], *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"treatment server exited early with {proc.returncode}")
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"treatment server did not listen on {host}:{port} within {timeout}s: {last_error}")


def start_treatment_server(
    arm: McpArm,
    *,
    log_path: Path,
    medplum_base_url: str | None,
    port: int,
) -> subprocess.Popen[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("w", encoding="utf-8")
    env = build_server_env(os.environ, arm=arm, medplum_base_url=medplum_base_url, port=port)
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "treatment_mcp_server.py")],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log.close()
    return proc


def stop_treatment_server(proc: subprocess.Popen[str] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def run_command(cmd: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=False, text=True)
    return proc.returncode


def file_entry(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    return {"path": str(path), "sha256": sha256_file(path)} if path.exists() else {"path": str(path), "missing": True}


def write_matrix_manifest(
    path: Path,
    *,
    args: argparse.Namespace,
    arms: list[McpArm],
    commands: dict[str, list[str]],
    results: list[dict[str, Any]],
) -> None:
    manifest = {
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "kind": "a9_mcp_matrix_manifest",
        "claim_test": "tool surface and skill condition vary in Codex MCP mode",
        "config": {
            "input": str(args.input),
            "out_dir": str(args.out_dir),
            "mcp_server_name": args.mcp_server_name,
            "mcp_url": args.mcp_url,
            "generic_subset": args.generic_subset,
            "bonfire_subset": args.bonfire_subset,
            "model": args.model,
            "profile": args.profile,
            "substrate": args.substrate,
            "limit": args.limit,
            "dry_run": args.dry_run,
            "start_server": args.start_server,
        },
        "files": {
            "input": file_entry(args.input),
            "fhir_skill": file_entry(args.fhir_skill_file),
            "treatment_mcp_server": file_entry(REPO_ROOT / "treatment_mcp_server.py"),
        },
        "arms": [asdict(arm) | {"skill_file": str(arm.skill_file) if arm.skill_file else None} for arm in arms],
        "commands": commands,
        "results": results,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run A9 Codex MCP/tool matrix.")
    parser.add_argument("--input", type=Path, default=Path("final_dataset/full_test409.csv"))
    parser.add_argument("--out-dir", type=Path, default=Path("runs/a9_mcp_matrix"))
    parser.add_argument("--schema", type=Path, default=Path("schemas/codex_answer.schema.json"))
    parser.add_argument("--fhir-skill-file", type=Path, default=DEFAULT_SKILL)
    parser.add_argument("--mcp-server-name", default="bonfire-eval")
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--generic-subset", default="control")
    parser.add_argument("--bonfire-subset", default="arm_full8")
    parser.add_argument("--medplum-base-url", default=os.environ.get("MEDPLUM_BASE_URL"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--server-startup-seconds", type=float, default=30.0)
    parser.add_argument("--start-server", action="store_true")
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
        raise SystemExit("live A9 runs require --live; keep --dry-run for prompt/manifest generation only")
    if not args.dry_run and not args.start_server:
        raise SystemExit("live A9 matrix runs must use --start-server so each arm gets the intended TOOL_SUBSET")
    if not args.dry_run and not args.allow_full_run and args.limit is None:
        raise SystemExit("unbounded live A9 runs require --allow-full-run or --limit")

    args.input = resolve_repo_path(args.input)
    args.out_dir = resolve_repo_path(args.out_dir)
    args.schema = resolve_repo_path(args.schema)
    args.fhir_skill_file = resolve_repo_path(args.fhir_skill_file)
    args.cwd = resolve_repo_path(args.cwd)

    arms = build_arms(
        fhir_skill_file=args.fhir_skill_file,
        generic_subset=args.generic_subset,
        bonfire_subset=args.bonfire_subset,
    )
    if not args.dry_run:
        verify_mcp_registration(codex_bin=args.codex_bin, server_name=args.mcp_server_name, expected_url=args.mcp_url)
    commands: dict[str, list[str]] = {}
    results: list[dict[str, Any]] = []
    overall = 0
    for arm in arms:
        arm_out = args.out_dir / arm.slug
        proc: subprocess.Popen[str] | None = None
        try:
            if args.start_server and not args.dry_run:
                proc = start_treatment_server(
                    arm,
                    log_path=arm_out / "treatment_server.log",
                    medplum_base_url=args.medplum_base_url,
                    port=args.port,
                )
                wait_for_tcp("127.0.0.1", args.port, proc, timeout=args.server_startup_seconds)

            cmd = build_codex_harness_command(
                arm,
                input_path=args.input,
                out_dir=args.out_dir,
                schema=args.schema,
                cwd=args.cwd,
                mcp_server_name=args.mcp_server_name,
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
            results.append({"arm_id": arm.arm_id, "slug": arm.slug, "tool_subset": arm.tool_subset, "returncode": rc, "status": status})
            if rc != 0:
                overall = rc
        except Exception as exc:
            results.append({"arm_id": arm.arm_id, "slug": arm.slug, "tool_subset": arm.tool_subset, "returncode": None, "status": "error", "error": str(exc)})
            overall = 1
        finally:
            stop_treatment_server(proc)

    write_matrix_manifest(args.out_dir / "matrix_manifest.json", args=args, arms=arms, commands=commands, results=results)
    print(json.dumps({"out_dir": str(args.out_dir), "arms": len(arms), "dry_run": args.dry_run, "status": overall}, indent=2))
    return overall


if __name__ == "__main__":
    raise SystemExit(main())
