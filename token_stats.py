#!/usr/bin/env python3
"""Token accounting per run dir: input/output/total from codex event streams.

Usage: python3 token_stats.py runs/codex-a6a-test409-run2 [more dirs...]
Emits per-dir totals + per-answer medians, and a combined table. Works on any
harness out-dir (answer runs, QT arms, grid cells).
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

USAGE_RE = re.compile(r'"input_tokens":\s*(\d+).*?"output_tokens":\s*(\d+)')


def dir_stats(run_dir: Path) -> dict:
    per_q: list[tuple[int, int]] = []
    for ev in run_dir.glob("questions/*/events.jsonl"):
        text = ev.read_text()
        inp = out = 0
        for m in USAGE_RE.finditer(text):
            inp += int(m.group(1))
            out += int(m.group(2))
        if inp or out:
            per_q.append((inp, out))
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
