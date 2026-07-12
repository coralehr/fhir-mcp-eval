#!/usr/bin/env python3
"""Multi-vote panel grading for the A6a confirmatory panel queue.

3-vote codex panel over batched items, arm-blind, cached, resumable.
Single-family (codex) panel — the same conservative-lower-bound convention the
repo used for A0' non-numeric labels; a cross-family check is a documented
follow-up (prereg §5 / ROADMAP item 15).

Usage:
  python3 panel_grade.py --queue runs/a6a-confirmatory-grading/panel_queue.jsonl \
      --cache runs/a6a-confirmatory-grading/panel_votes.json --live
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "correct": {"type": "boolean"},
                },
                "required": ["item_id", "correct"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}

JUDGE_PREAMBLE = """You are grading clinical question-answering outputs against gold answers.
For each item decide if the model answer is CORRECT with respect to the gold.

Rules:
- Numeric golds: allow ~1% relative tolerance (floor 0.05). Units may be implied.
- Golds like [[1]]/[[0]] on a yes/no-phrased question mean Yes/No; on a count-phrased
  question they mean the count. Read the question to disambiguate.
- Categorical golds: the answer must contain the gold value(s); case/format-insensitive;
  clinically equivalent phrasing counts.
- Timestamp golds: same instant counts even if formatted differently; date-only match is
  acceptable when the question asks for a date.
- Gold timestamps ending 00:00:00 are often DATE-ONLY placeholders (the source table has no
  time component): an answer giving the same date with a different or more precise time is CORRECT.
- Verbalized signed differences count: "decreased by 0.1" means -0.1; "2.1 lower" means -2.1.
- An answer declaring insufficiency/abstention is CORRECT only if the gold is empty
  (unanswerable); otherwise it is incorrect.
- Judge ONLY correctness against the gold. Ignore style, length, citations.

Return JSON: {"verdicts": [{"item_id": "...", "correct": true|false}, ...]} covering EVERY item.

ITEMS:
"""


def load_queue(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open()]


def cache_key(item: dict) -> str:
    return f"{item['arm']}|{item['question_id']}"


def batch_prompt(batch: list[tuple[str, dict]]) -> str:
    lines = [JUDGE_PREAMBLE]
    for item_id, item in batch:
        lines.append(
            json.dumps(
                {
                    "item_id": item_id,
                    "question": item.get("question"),
                    "gold": item.get("gold"),
                    "model_answer": item.get("answer"),
                    "insufficiency_reason": item.get("insufficiency_reason"),
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)


def run_vote(batch: list[tuple[str, dict]], *, codex_bin: str, timeout: int) -> dict[str, bool]:
    with tempfile.TemporaryDirectory() as td:
        schema_path = Path(td) / "schema.json"
        out_path = Path(td) / "out.json"
        schema_path.write_text(json.dumps(BATCH_SCHEMA))
        cmd = [
            codex_bin,
            "exec",
            "--skip-git-repo-check",
            "--json",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(out_path),
            "-C",
            td,
            "-s",
            "read-only",
            batch_prompt(batch),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if not out_path.exists():
            raise RuntimeError(f"no panel output (rc={proc.returncode}): {proc.stderr[-200:]}")
        verdicts = json.loads(out_path.read_text()).get("verdicts", [])
        return {v["item_id"]: bool(v["correct"]) for v in verdicts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", type=Path, default=Path("runs/a6a-confirmatory-grading/panel_queue.jsonl"))
    ap.add_argument("--cache", type=Path, default=Path("runs/a6a-confirmatory-grading/panel_votes.json"))
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--votes", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--codex-bin", default="codex")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()

    queue = load_queue(args.queue)
    votes: dict[str, list[bool]] = {}
    if args.cache.exists():
        votes = {k: list(v) for k, v in json.loads(args.cache.read_text()).items()}

    pending = [item for item in queue if len(votes.get(cache_key(item), [])) < args.votes]
    print(f"queue={len(queue)} fully-voted={len(queue) - len(pending)} pending={len(pending)}")
    if not args.live:
        print("dry run — pass --live to grade")
        return 0

    for vote_round in range(args.votes):
        todo = [item for item in queue if len(votes.get(cache_key(item), [])) <= vote_round]
        for i in range(0, len(todo), args.batch_size):
            batch_items = todo[i : i + args.batch_size]
            batch = [(cache_key(it), it) for it in batch_items]
            try:
                result = run_vote(batch, codex_bin=args.codex_bin, timeout=args.timeout)
            except Exception as exc:
                print(f"vote round {vote_round} batch {i // args.batch_size}: FAILED {exc}")
                # persist and stop — resumable
                args.cache.write_text(json.dumps(votes, indent=0, sort_keys=True))
                return 3
            for key, correct in result.items():
                votes.setdefault(key, [])
                if len(votes[key]) <= vote_round:
                    votes[key].append(correct)
            args.cache.write_text(json.dumps(votes, indent=0, sort_keys=True))
            done_now = sum(1 for it in queue if len(votes.get(cache_key(it), [])) >= args.votes)
            print(f"round {vote_round + 1}/{args.votes} batch {i // args.batch_size + 1}: cached; fully-voted {done_now}/{len(queue)}", flush=True)

    majority = {k: (sum(v) * 2 > len(v)) for k, v in votes.items() if len(v) >= args.votes}
    out = args.cache.with_name("panel_verdicts.json")
    out.write_text(json.dumps({k: int(v) for k, v in sorted(majority.items())}, indent=0) + "\n")
    print(f"PANEL_DONE: {len(majority)} verdicts -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
