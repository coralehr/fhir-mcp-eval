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

import codex_harness
from paired_stats import paired_summary
from question_selection import load_scheduled_question_ids, select_question_rows

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


GRADER_VERSION = "det-v2"  # v2: verbalized-sign equivalence (A6A_ARTIFACT_REVIEW.md)

_DECREASE = re.compile(
    r"\b(?:decreas\w+|dropp?\w+|fell|fall\w*|declin\w+|reduc\w+|lower\w*|lost|down)\b[^.;]{0,40}?\bby\s+(\d+\.?\d*)"
)
_INCREASE = re.compile(
    r"\b(?:increas\w+|rose|risen|rais\w+|gain\w+|grew|higher|up)\b[^.;]{0,40}?\bby\s+(\d+\.?\d*)"
)
_X_LOWER = re.compile(r"\b(\d+\.?\d*)\s*(?:[a-zA-Z/%µ]+\s+)?(?:lower|less|below|fewer)\b")
_X_HIGHER = re.compile(r"\b(\d+\.?\d*)\s*(?:[a-zA-Z/%µ]+\s+)?(?:higher|more|above|greater)\b")


def signed_values(ans: str) -> list[float]:
    """Signed magnitudes expressed verbally: 'decreased by 0.1' -> -0.1,
    '2.1 K/uL lower' -> -2.1. Run-1's grader missed these (false negatives)."""
    text = (ans or "").lower()
    out: list[float] = []
    for m in _DECREASE.finditer(text):
        out.append(-float(m.group(1)))
    for m in _X_LOWER.finditer(text):
        out.append(-float(m.group(1)))
    for m in _INCREASE.finditer(text):
        out.append(float(m.group(1)))
    for m in _X_HIGHER.finditer(text):
        out.append(float(m.group(1)))
    return out


def numeric_match(ans: str, gold: str) -> bool | None:
    gnums = gold_numbers(gold)
    anums = [float(x) for x in NUM.findall(ans or "")] + signed_values(ans)
    if not gnums:
        return None
    for gn in gnums:
        tol = max(0.05, 0.01 * abs(gn))
        if not any(abs(an - gn) <= tol for an in anums):
            return False
    return True


# --- end lifted section (numeric_match extended to det-v2) ---

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
        question_dir = run_dir / "questions" / qid
        answer_path = question_dir / "answer.json"
        if codex_harness.terminal_question_status(question_dir) == "contaminated":
            verdicts[qid] = 0
            continue
        if not answer_path.exists():
            if not answered_only:
                verdicts[qid] = 0  # canonical: failures score 0
            continue
        event_log_path = answer_path.with_name("events.jsonl")
        if codex_harness.audit_event_log(event_log_path)["contaminated"]:
            # A schema-valid answer derived through a command/tool is not a
            # frozen-packet answer. Canonical grading treats invalid attempts
            # like other harness failures rather than crediting leaked data.
            verdicts[qid] = 0
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
    ap.add_argument("--question-spec", type=Path, default=None, help="JSON list or object containing scheduled question_ids")
    ap.add_argument("--question-id", action="append", default=[], help="restrict grading to this scheduled question ID (repeatable)")
    args = ap.parse_args()

    try:
        scheduled_ids = load_scheduled_question_ids(
            spec_path=args.question_spec,
            repeated_ids=args.question_id,
        )
        with args.input.open(newline="", encoding="utf-8") as handle:
            all_gold = {r["question_id"]: r for r in csv.DictReader(handle)}
        gold = select_question_rows(all_gold, scheduled_ids)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        ap.error(str(exc))
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
    if scheduled_ids is not None:
        partial["scheduled_question_count"] = len(gold)
        partial["explicit_question_schedule"] = True
    (args.out / "partial_summary.json").write_text(json.dumps(partial, indent=1) + "\n")
    print(json.dumps({k: v for k, v in partial.items() if k != "paired"}, indent=1))
    if partial["paired"]:
        p = partial["paired"]
        print(f"det-subset paired: A6a {p['acc_a']:.1%} vs A0' {p['acc_b']:.1%} | diff {p['diff']:+.1%} | McNemar p={p['mcnemar_p']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
