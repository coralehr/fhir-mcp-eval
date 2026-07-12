#!/usr/bin/env python3
"""Token accounting per run dir: input/output/total from codex event streams.

Usage: python3 token_stats.py runs/codex-a6a-test409-run2 [more dirs...]
Emits per-dir totals + per-answer medians, and a combined table. Works on any
harness out-dir (answer runs, QT arms, grid cells).
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def usage_for_event_log(event_path: Path) -> tuple[int, int] | None:
    """Return the first complete turn's usage, ignoring race-corrupted debris."""

    for line in event_path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        try:
            inp = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
            out = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
        except (TypeError, ValueError):
            continue
        if inp or out:
            return inp, out
    return None


def dir_stats(run_dir: Path) -> dict:
    per_q: list[tuple[int, int]] = []
    for ev in run_dir.glob("questions/*/events.jsonl"):
        usage = usage_for_event_log(ev)
        if usage:
            per_q.append(usage)
    if not per_q:
        return {"dir": str(run_dir), "answers": 0}
    totals_in = sum(i for i, _ in per_q)
    totals_out = sum(o for _, o in per_q)
    return {
        "dir": str(run_dir),
        "answers": len(per_q),
        "input_tokens": totals_in,
        "output_tokens": totals_out,
        "total_tokens": totals_in + totals_out,
        "median_total_per_answer": int(statistics.median(i + o for i, o in per_q)),
        "p90_total_per_answer": int(sorted(i + o for i, o in per_q)[int(len(per_q) * 0.9)]),
    }


def main() -> int:
    dirs = [Path(d) for d in sys.argv[1:]] or sorted(Path("runs").glob("codex-*"))
    rows = [dir_stats(d) for d in dirs if d.is_dir()]
    for r in rows:
        if r["answers"]:
            print(
                f"{r['dir']}: n={r['answers']} total={r['total_tokens']:,} "
                f"(in={r['input_tokens']:,} out={r['output_tokens']:,}) "
                f"median/ans={r['median_total_per_answer']:,} p90={r['p90_total_per_answer']:,}"
            )
        else:
            print(f"{r['dir']}: no usage data")
    print(json.dumps(rows, indent=1), file=open("runs/token_stats.json", "w"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
