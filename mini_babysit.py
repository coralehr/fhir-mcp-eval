#!/usr/bin/env python3
"""Quota-aware babysitter v2 (mini): pinned-model pipeline.

Stages: run-2 completion -> QT-1 -> QT-2 -> QT-3 -> generality grid (if the
grid spec file exists). Every codex call pinned via A6A_MODEL/A6A_EFFORT or
explicit harness flags. On a quota wall: parse "try again at H:MM", sleep to
reset + 3 min, relaunch. Everything resumable; nothing deleted.
"""

from __future__ import annotations

import atexit
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
import time

from codex_harness import terminal_question_status
from run_lock import AlreadyRunning, LOCK_BUSY_EXIT, acquire_single_instance

ROOT = pathlib.Path.home() / "fhir-mcp-eval-qt"
os.chdir(ROOT)
PY = sys.executable
PIN_MODEL = os.environ.get("PIN_MODEL", "gpt-5.6-sol")
PIN_EFFORT = os.environ.get("PIN_EFFORT", "high")


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%m-%d %H:%M:%S}] {msg}", flush=True)


try:
    PIPELINE_LOCK = acquire_single_instance(ROOT / "runs/.mini_babysit.lock")
except AlreadyRunning as exc:
    log(f"ALREADY_RUNNING: {exc}")
    raise SystemExit(LOCK_BUSY_EXIT) from exc
atexit.register(PIPELINE_LOCK.close)


def newest_reset_time() -> dt.datetime | None:
    pats = []
    for d in ROOT.glob("runs/codex-*/questions/*/events.jsonl"):
        try:
            if time.time() - d.stat().st_mtime < 3600:
                pats += re.findall(r"try again at (\d{1,2}):(\d{2})\s*(AM|PM)", d.read_text()[-600:])
        except Exception:
            pass
    if not pats:
        return None
    h, m, ap = pats[-1]
    h, m = int(h), int(m)
    if ap == "PM" and h != 12:
        h += 12
    if ap == "AM" and h == 12:
        h = 0
    now = dt.datetime.now()
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if t <= now:
        t += dt.timedelta(days=1)
    return t


def wait_for_reset() -> None:
    t = newest_reset_time()
    if t is None:
        log("no reset time parseable; sleeping 30 min")
        time.sleep(1800)
        return
    delay = max(60, (t - dt.datetime.now()).total_seconds() + 180)
    log(f"quota wall; sleeping until {t:%H:%M} ({int(delay / 60)} min)")
    time.sleep(delay)


def terminal_attempts(out_dir: str) -> int:
    question_dirs = pathlib.Path(out_dir).glob("questions/*")
    return sum(
        1
        for question_dir in question_dirs
        if terminal_question_status(question_dir) is not None
    )


def drive(label: str, cmd: list[str], out_dirs: list[str], target: int, env: dict | None = None) -> None:
    while True:
        before = sum(terminal_attempts(d) for d in out_dirs)
        if before >= target:
            log(f"{label}: COMPLETE ({before}/{target})")
            return
        log(f"{label}: launching ({before}/{target})")
        result = subprocess.run(cmd, env=env)
        if result.returncode == LOCK_BUSY_EXIT:
            log(f"{label}: another controller owns the run lock; retrying in 30s")
            time.sleep(30)
            continue
        after = sum(terminal_attempts(d) for d in out_dirs)
        if after >= target:
            log(f"{label}: COMPLETE ({after}/{target})")
            return
        if after == before:
            wait_for_reset()
        else:
            time.sleep(30)


def run_grading(label: str, cmd: list[str], done_marker: pathlib.Path) -> None:
    """Run a grading command with quota-wall retry; skip if done_marker exists."""
    if done_marker.exists():
        log(f"{label}: already done ({done_marker})")
        return
    attempts = 0
    while True:
        attempts += 1
        log(f"{label}: attempt {attempts}")
        r = subprocess.run(cmd)
        if done_marker.exists() or r.returncode == 0:
            log(f"{label}: DONE")
            return
        if attempts > 40:
            log(f"{label}: giving up after 40 attempts")
            return
        wait_for_reset()


PANEL_PIN = ["--model", PIN_MODEL, "--reasoning-effort", PIN_EFFORT]


def grade_pair(
    tag: str,
    a_dir: str,
    b_dir: str,
    *,
    question_spec: pathlib.Path | None = None,
    a_packets: pathlib.Path | str | None = None,
    b_packets: pathlib.Path | str | None = None,
) -> None:
    """Full grading for a pair of answer dirs: det pass -> pinned panel -> assembly."""
    gdir = ROOT / f"runs/{tag}-grading"
    selection_args = ["--question-spec", str(question_spec)] if question_spec else []
    run_grading(
        f"{tag}:det",
        [
            PY,
            "grade_a6a_confirmatory.py",
            "--a6a-dir",
            a_dir,
            "--a0prime-dir",
            b_dir,
            "--out",
            str(gdir),
            *selection_args,
        ],
        gdir / "det_verdicts.json",
    )
    run_grading(
        f"{tag}:panel",
        [PY, "panel_grade.py", "--queue", str(gdir / "panel_queue.jsonl"), "--cache", str(gdir / "panel_votes.json"), "--live", *PANEL_PIN],
        gdir / "panel_verdicts.json",
    )
    assemble_cmd = [
        PY,
        "final_confirmatory_result.py",
        "--grading-dir",
        str(gdir),
        "--a6a-dir",
        a_dir,
        "--a0prime-dir",
        b_dir,
        *selection_args,
    ]
    if a_packets is not None:
        assemble_cmd += ["--a6a-packets", str(a_packets)]
    if b_packets is not None:
        assemble_cmd += ["--a0prime-packets", str(b_packets)]
    run_grading(
        f"{tag}:assemble",
        assemble_cmd,
        gdir / "final_result.json",
    )


# Stage 1: run-2 remainder, pinned
env = dict(os.environ, A6A_RUN_SUFFIX="-run2", A6A_MODEL=PIN_MODEL, A6A_EFFORT=PIN_EFFORT)
drive(
    "run2",
    [PY, "run_a6a_confirmatory.py", "--chunk-size", "10", "--timeout", "420"],
    ["runs/codex-a6a-test409-run2", "runs/codex-a0prime-test409-run2"],
    818,
    env=env,
)

# Stage 1b: grade run-2 (det -> pinned panel -> assembly)
grade_pair("run2-final", "runs/codex-a6a-test409-run2", "runs/codex-a0prime-test409-run2")

# Stage 2: QT arms, pinned
for feat in ("include-pinning", "agg-summary", "endpoint-reserve"):
    pkt = ROOT / f"runs/qt_{feat}_test409_packets.jsonl"
    out = f"runs/codex-qt-{feat}-test409"
    if not pkt.exists():
        log(f"qt-{feat}: packets missing, skipping")
        continue
    qids = [json.loads(line)["question_id"] for line in open(pkt)]
    cmd = [
        PY, "codex_harness.py", "--mode", "packet",
        "--packet-json", str(pkt), "--input", "final_dataset/full_test409.csv",
        "--out-dir", out, "--timeout", "420", "--live", "--skip-existing",
        "--allow-full-run", "--model", PIN_MODEL, "--reasoning-effort", PIN_EFFORT,
    ]
    for q in qids:
        cmd += ["--question-id", q]
    drive(f"qt-{feat}", cmd, [out], len(qids))
    # Stage 2b: grade this QT arm paired against the run-2 A6a baseline
    grade_pair(
        f"qt-{feat}",
        out,
        "runs/codex-a6a-test409-run2",
        a_packets=pkt,
        b_packets=ROOT / "runs/a6a_test409_packets.jsonl",
    )

# Stage 3: generality grid, if spec present
grid_spec = ROOT / "grid_spec.json"
if grid_spec.exists():
    spec = json.loads(grid_spec.read_text())
    qids = spec["question_ids"]
    for cell in spec["cells"]:
        model, effort = cell["model"], cell["effort"]
        for arm in ("a6a", "a0prime"):
            pkt = f"runs/{'a6a' if arm == 'a6a' else 'a0prime'}_test409_packets.jsonl"
            out = f"runs/grid-{arm}-{model.replace('.', '')}-{effort}"
            cmd = [
                PY, "codex_harness.py", "--mode", "packet",
                "--packet-json", pkt, "--input", "final_dataset/full_test409.csv",
                "--out-dir", out, "--timeout", "600", "--live", "--skip-existing",
                "--allow-full-run", "--model", model, "--reasoning-effort", effort,
            ]
            for q in qids:
                cmd += ["--question-id", q]
            drive(f"grid-{arm}-{model}-{effort}", cmd, [out], len(qids))
    # Stage 3b: grade each grid cell pair (a6a vs a0prime within the cell)
    for cell in spec["cells"]:
        model, effort = cell["model"], cell["effort"]
        mtag = model.replace(".", "")
        grade_pair(
            f"grid-{mtag}-{effort}",
            f"runs/grid-a6a-{mtag}-{effort}",
            f"runs/grid-a0prime-{mtag}-{effort}",
            question_spec=grid_spec,
            a_packets=ROOT / "runs/a6a_test409_packets.jsonl",
            b_packets=ROOT / "runs/a0prime_test409_packets.jsonl",
        )
else:
    log("no grid_spec.json — grid stage skipped")

log("ALL_STAGES_COMPLETE")
