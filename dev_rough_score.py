#!/usr/bin/env python3
"""ROUGH dev-slice scorer — mechanics debugging only, never a claim.

Deterministic containment check of the gold value in the answer text.
Boolean golds ([[1]]/[[0]]) map to affirmative/negative cue words. This is
NOT the trustworthy grading pipeline (build_labels/final_grade) and its
numbers must never leave the dev loop.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
from pathlib import Path


def parse_gold(raw: str):
    try:
        value = ast.literal_eval(raw)
    except Exception:
        return None
    if isinstance(value, list) and value and isinstance(value[0], list):
        flat = [x for sub in value for x in sub]
        return flat
    return value if isinstance(value, list) else [value]


def numbers_in(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", text)]


AFFIRM = ("yes", "did receive", "was prescribed", "underwent", "did undergo", "did have", "prescribed once", "once", "was admitted")
NEGATE = ("no.", "no,", "did not", "was not", "never", "not prescribed", "insufficient")


def rough_match(gold, answer_text: str, insufficiency: str | None) -> str:
    text = (answer_text or "").lower()
    if gold is None:
        return "ungraded"
    if insufficiency:
        return "abstained"
    if all(isinstance(g, (int, float)) for g in gold) and gold:
        got = numbers_in(text)
        if len(gold) == 1 and float(gold[0]) in (0.0, 1.0):
            g = float(gold[0])
            if g == 1.0:
                return "correct" if (1.0 in got or any(a in text for a in AFFIRM)) and not any(n in text for n in NEGATE) else "wrong"
            return "correct" if any(n in text for n in NEGATE) or 0.0 in got else "wrong"
        ok = all(any(abs(float(g) - v) <= max(0.01, abs(float(g)) * 0.01) for v in got) for g in gold)
        return "correct" if ok else "wrong"
    ok = all(str(g).lower() in text for g in gold)
    return "correct" if ok else "wrong"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--input", type=Path, default=Path("final_dataset/questions_answers_sql_fhir.csv"))
    args = ap.parse_args()

    gold_by_qid = {r["question_id"]: r for r in csv.DictReader(args.input.open())}
    tallies: dict[str, int] = {}
    rows = []
    for answer_path in sorted(args.run_dir.glob("questions/*/answer.json")):
        qid = answer_path.parent.name
        answer = json.loads(answer_path.read_text())
        gold_row = gold_by_qid.get(qid)
        if not gold_row:
            continue
        verdict = rough_match(parse_gold(gold_row["true_answer"]), answer.get("answer", ""), answer.get("insufficiency_reason"))
        tallies[verdict] = tallies.get(verdict, 0) + 1
        rows.append({"question_id": qid, "verdict": verdict, "gold": gold_row["true_answer"][:40], "answer": (answer.get("answer") or "")[:100]})
    out = {"run_dir": str(args.run_dir), "n": len(rows), "tallies": tallies, "rough_accuracy_lower_bound": (tallies.get("correct", 0) / len(rows)) if rows else None}
    (args.run_dir / "rough_score.json").write_text(json.dumps({"summary": out, "rows": rows}, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
