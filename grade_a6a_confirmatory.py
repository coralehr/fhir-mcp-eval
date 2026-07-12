#!/usr/bin/env python3
"""Grade the A6a-vs-A0' confirmatory runs (prereg docs/prereg/A6A.md §5-§6).

Deterministic pass now; panel queue for everything the deterministic rules
cannot label. Grading rules are lifted verbatim from build_labels.py (the
repo's trustworthy pipeline) — gold_type / gold_numbers / numeric_match —
because that module executes at import.

Outputs (under --out, default runs/a6a-confirmatory-grading/):
  det_verdicts.json     per-arm deterministic verdicts {qid: 0|1}
  panel_queue.jsonl     items needing the multi-vote panel (arm-blind text)
  partial_summary.json  paired stats on the deterministically-graded subset
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from paired_stats import paired_summary

# --- lifted from build_labels.py (see module docstring) ---
NUM = re.compile(r"-?\d+\.?\d*")


def gold_type(g: str) -> str:
    g = (g or "").strip()
    if g in ("[[1]]", "[[0]]", "[['1']]", "[['0']]", "[1]", "[0]"):
        return "boolean"
    if "'" in g or '"' in g:
        return "categorical"
    inner = re.sub(r"[\[\]]", " ", g)
    toks = [t for t in re.split(r"[\s,]+", inner) if t]
    if toks and all(re.fullmatch(r"-?\d+\.?\d*", t) for t in toks):
        return "numeric"
    return "other"


def gold_numbers(g: str) -> list[float]:
    return [float(x) for x in NUM.findall(g or "")]


def numeric_match(ans: str, gold: str) -> bool | None:
    gnums = gold_numbers(gold)
    anums = [float(x) for x in NUM.findall(ans or "")]
    if not gnums:
        return None
    for gn in gnums:
        tol = max(0.05, 0.01 * abs(gn))
        if not any(abs(an - gn) <= tol for an in anums):
            return False
    return True


# --- end lifted section ---

EMPTY_GOLDS = ("", "[]", "nan", "None", "[[]]")


def grade_arm(run_dir: Path, gold: dict[str, dict], *, answered_only: bool = False) -> tuple[dict[str, int], list[dict]]:
    """Deterministic verdicts + panel queue for one arm.

    answered_only=True skips unanswered questions instead of scoring them 0 —
    for partial mid-quota progress views ONLY. The canonical final grading
    (prereg §5) always uses answered_only=False: failures score 0.
    """
    verdicts: dict[str, int] = {}
    panel: list[dict] = []
    for qid, row in gold.items():
        answer_path = run_dir / "questions" / qid / "answer.json"
        if not answer_path.exists():
            if not answered_only:
                verdicts[qid] = 0  # canonical: failures score 0
            continue
        ans = json.loads(answer_path.read_text())
        text = ans.get("answer") or ""
        insufficiency = ans.get("insufficiency_reason")
        g = (row.get("true_answer") or "").strip()

        if g in EMPTY_GOLDS:
            # unanswerable-by-design: an explicit insufficiency signal is
            # deterministically correct; an attempted answer goes to the panel
            if insufficiency:
                verdicts[qid] = 1
            else:
                panel.append(_panel_item(qid, row, text, insufficiency))
            continue

        gtype = gold_type(g)
        if gtype == "numeric":
            # abstention on an answerable numeric gold is deterministically wrong
            if insufficiency and not text.strip():
                verdicts[qid] = 0
                continue
            m = numeric_match(text, g)
            if m is None:
                panel.append(_panel_item(qid, row, text, insufficiency))
            else:
                verdicts[qid] = 1 if m else 0
            continue

        # boolean / categorical / other -> multi-vote panel (sees the question,
        # never the arm identity)
        panel.append(_panel_item(qid, row, text, insufficiency))
    return verdicts, panel


def _panel_item(qid: str, row: dict, text: str, insufficiency) -> dict:
    return {
        "question_id": qid,
        "question": row.get("question"),
        "gold": row.get("true_answer"),
        "answer": text,
        "insufficiency_reason": insufficiency,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a6a-dir", type=Path, default=Path("runs/codex-a6a-test409"))
    ap.add_argument("--a0prime-dir", type=Path, default=Path("runs/codex-a0prime-test409"))
    ap.add_argument("--input", type=Path, default=Path("final_dataset/full_test409.csv"))
    ap.add_argument("--out", type=Path, default=Path("runs/a6a-confirmatory-grading"))
    ap.add_argument("--answered-only", action="store_true", help="partial progress view: skip unanswered instead of scoring 0 (never the final grading)")
    args = ap.parse_args()

    gold = {r["question_id"]: r for r in csv.DictReader(args.input.open())}
    args.out.mkdir(parents=True, exist_ok=True)

    arms = {"a6a": args.a6a_dir, "a0prime": args.a0prime_dir}
    det: dict[str, dict[str, int]] = {}
    queues: dict[str, list[dict]] = {}
    for name, run_dir in arms.items():
        verdicts, panel = grade_arm(run_dir, gold, answered_only=args.answered_only)
        det[name] = verdicts
        queues[name] = panel

    (args.out / "det_verdicts.json").write_text(json.dumps(det, indent=1, sort_keys=True) + "\n")
    with (args.out / "panel_queue.jsonl").open("w") as f:
        for name, items in queues.items():
            for item in items:
                f.write(json.dumps({"arm": name, **item}, sort_keys=True) + "\n")

    # paired partial summary on the intersection graded deterministically in BOTH arms
    both = sorted(set(det["a6a"]) & set(det["a0prime"]))
    pairs = [(gold[q]["patient_fhir_id"], det["a6a"][q], det["a0prime"][q]) for q in both]
    partial = {
        "note": "DETERMINISTIC SUBSET ONLY — panel labels pending; not the prereg primary result",
        "answered_only": args.answered_only,
        "det_graded_both_arms": len(both),
        "panel_pending": {k: len(v) for k, v in queues.items()},
        "paired": paired_summary(pairs) if len(pairs) >= 2 else None,
    }
    (args.out / "partial_summary.json").write_text(json.dumps(partial, indent=1) + "\n")
    print(json.dumps({k: v for k, v in partial.items() if k != "paired"}, indent=1))
    if partial["paired"]:
        p = partial["paired"]
        print(f"det-subset paired: A6a {p['acc_a']:.1%} vs A0' {p['acc_b']:.1%} | diff {p['diff']:+.1%} | McNemar p={p['mcnemar_p']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
