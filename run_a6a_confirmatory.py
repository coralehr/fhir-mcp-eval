#!/usr/bin/env python3
"""Confirmatory A6a-vs-A0' run driver (prereg docs/prereg/A6A.md).

Runs both arms through codex_harness in small interleaved chunks so that a
quota interruption leaves the two arms nearly balanced — pairing survives
partial completion. Fully resumable: every chunk runs with --skip-existing,
so re-invoking after a quota window continues where it stopped.

Usage:
  python3 run_a6a_confirmatory.py                # run/resume everything
  python3 run_a6a_confirmatory.py --chunk-size 10 --max-chunks 4
  python3 run_a6a_confirmatory.py --status       # progress only, no runs
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import os

from codex_harness import terminal_question_status
from run_lock import AlreadyRunning, LOCK_BUSY_EXIT, acquire_single_instance

RUN_SUFFIX = os.environ.get("A6A_RUN_SUFFIX", "")  # e.g. "-run2" for the prompt-fix rerun

ARMS = [
    {
        "name": "a6a",
        "packets": Path("runs/a6a_test409_packets.jsonl"),
        "out_dir": Path(f"runs/codex-a6a-test409{RUN_SUFFIX}"),
    },
    {
        "name": "a0prime",
        "packets": Path("runs/a0prime_test409_packets.jsonl"),
        "out_dir": Path(f"runs/codex-a0prime-test409{RUN_SUFFIX}"),
    },
]
INPUT_CSV = Path("final_dataset/full_test409.csv")
LOCK_PATH = Path("runs") / f".run_a6a_confirmatory{RUN_SUFFIX or '-default'}.lock"


def qids_for(packets: Path) -> list[str]:
    return [json.loads(line)["question_id"] for line in packets.open()]


def terminal_question_ids(out_dir: Path) -> set[str]:
    return {
        question_dir.name
        for question_dir in out_dir.glob("questions/*")
        if terminal_question_status(question_dir) is not None
    }


def failed_marker(out_dir: Path, qid: str) -> bool:
    events = out_dir / "questions" / qid / "events.jsonl"
    if not events.exists():
        return False
    tail = events.read_text()[-500:]
    return "usage limit" in tail


def run_chunk(arm: dict, chunk: list[str], *, timeout: int) -> bool:
    """Returns False if the quota wall was hit (caller should stop)."""
    cmd = [
        sys.executable,
        "codex_harness.py",
        "--mode",
        "packet",
        "--packet-json",
        str(arm["packets"]),
        "--input",
        str(INPUT_CSV),
        "--out-dir",
        str(arm["out_dir"]),
        "--timeout",
        str(timeout),
        "--live",
        "--skip-existing",
        "--allow-full-run",
    ]
    if os.environ.get("A6A_MODEL"):
        cmd += ["--model", os.environ["A6A_MODEL"]]
    if os.environ.get("A6A_EFFORT"):
        cmd += ["--reasoning-effort", os.environ["A6A_EFFORT"]]
    for qid in chunk:
        cmd += ["--question-id", qid]
    subprocess.run(cmd, check=False)
    # quota detection: every question in the chunk failed with a usage-limit event
    done = terminal_question_ids(arm["out_dir"])
    missing = [q for q in chunk if q not in done]
    if missing and all(failed_marker(arm["out_dir"], q) for q in missing):
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk-size", type=int, default=10)
    ap.add_argument("--max-chunks", type=int, default=None, help="stop after N chunks per arm (spend control)")
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    state = []
    for arm in ARMS:
        if not arm["packets"].exists():
            print(f"ERROR: {arm['packets']} missing — build confirmatory packets first")
            return 2
        qids = qids_for(arm["packets"])
        done = terminal_question_ids(arm["out_dir"])
        state.append({"arm": arm, "qids": qids, "done": done})
        print(f"{arm['name']}: {len(done)}/{len(qids)} terminal attempts")
    if args.status:
        return 0

    try:
        run_lock = acquire_single_instance(LOCK_PATH)
    except AlreadyRunning as exc:
        print(f"ALREADY_RUNNING: {exc}")
        return LOCK_BUSY_EXIT

    with run_lock:
        chunks_run = 0
        while True:
            progressed = False
            for st in state:
                remaining = [
                    q
                    for q in st["qids"]
                    if q not in terminal_question_ids(st["arm"]["out_dir"])
                ]
                if not remaining:
                    continue
                chunk = remaining[: args.chunk_size]
                print(f"[{st['arm']['name']}] chunk of {len(chunk)} ({len(remaining)} remaining)")
                ok = run_chunk(st["arm"], chunk, timeout=args.timeout)
                if not ok:
                    print("QUOTA_WALL: all questions in the last chunk failed on usage limit — stopping; re-run to resume")
                    return 3
                progressed = True
            chunks_run += 1
            if args.max_chunks and chunks_run >= args.max_chunks:
                print(f"max-chunks {args.max_chunks} reached — stopping (resumable)")
                return 0
            if not progressed:
                break

        print("ALL_COMPLETE")
        for st in state:
            done = terminal_question_ids(st["arm"]["out_dir"])
            print(f"{st['arm']['name']}: {len(done)}/{len(st['qids'])} terminal attempts")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
