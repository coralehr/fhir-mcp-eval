#!/usr/bin/env python3
"""Assemble the A6a-vs-A0' primary confirmatory result (prereg §2, §6, §7).

Combines deterministic verdicts + panel majority verdicts into full-409
per-arm labels (failures already scored 0 by the deterministic pass), then
emits the pre-registered paired analysis plus the exploratory strata and
secondary metrics.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from grade_a6a_confirmatory import gold_type
from paired_stats import paired_summary

ARMS = ("a6a", "a0prime")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grading-dir", type=Path, default=Path("runs/a6a-confirmatory-grading"))
    ap.add_argument("--input", type=Path, default=Path("final_dataset/full_test409.csv"))
    ap.add_argument("--a6a-packets", type=Path, default=Path("runs/a6a_test409_packets.jsonl"))
    ap.add_argument("--a0prime-packets", type=Path, default=Path("runs/a0prime_test409_packets.jsonl"))
    args = ap.parse_args()

    gold = {r["question_id"]: r for r in csv.DictReader(args.input.open())}
    det = json.loads((args.grading_dir / "det_verdicts.json").read_text())
    panel = json.loads((args.grading_dir / "panel_verdicts.json").read_text())

    labels: dict[str, dict[str, int]] = {}
    missing: dict[str, list[str]] = {a: [] for a in ARMS}
    for arm in ARMS:
        labels[arm] = {}
        for qid in gold:
            if qid in det.get(arm, {}):
                labels[arm][qid] = det[arm][qid]
            elif f"{arm}|{qid}" in panel:
                labels[arm][qid] = panel[f"{arm}|{qid}"]
            else:
                missing[arm].append(qid)

    if any(missing.values()):
        print(json.dumps({"INCOMPLETE": {a: len(v) for a, v in missing.items()}}))
        return 2

    qids = sorted(gold)
    pairs = [(gold[q]["patient_fhir_id"], labels["a6a"][q], labels["a0prime"][q]) for q in qids]
    primary = paired_summary(pairs)

    # exploratory strata (prereg §7): answer type + source domain
    strata: dict[str, dict] = {}
    for name, keyfn in [
        ("answer_type", lambda q: gold_type((gold[q].get("true_answer") or "").strip()) if (gold[q].get("true_answer") or "").strip() not in ("", "[]", "nan", "None", "[[]]") else "unanswerable"),
        ("source_domain", lambda q: gold[q].get("main_table_name") or "?"),
    ]:
        groups: dict[str, list] = {}
        for q in qids:
            groups.setdefault(keyfn(q), []).append((gold[q]["patient_fhir_id"], labels["a6a"][q], labels["a0prime"][q]))
        strata[name] = {
            k: {
                "n": len(v),
                "a6a": round(sum(a for _, a, _ in v) / len(v), 4),
                "a0prime": round(sum(b for _, _, b in v) / len(v), 4),
            }
            for k, v in sorted(groups.items())
            if len(v) >= 5
        }

    # secondary metrics: abstentions + packet economics
    def arm_secondary(run_dir: Path, packets: Path) -> dict:
        abst = 0
        answered = 0
        for qid in qids:
            p = run_dir / "questions" / qid / "answer.json"
            if not p.exists():
                continue
            answered += 1
            if json.loads(p.read_text()).get("insufficiency_reason"):
                abst += 1
        chars = [
            (json.loads(line)["packet"].get("bounds") or {}).get("char_count", 0)
            for line in packets.open()
        ]
        chars.sort()
        return {
            "answered": answered,
            "abstentions": abst,
            "packet_chars_median": chars[len(chars) // 2],
            "packet_chars_total": sum(chars),
        }

    secondary = {
        "a6a": arm_secondary(Path("runs/codex-a6a-test409"), args.a6a_packets),
        "a0prime": arm_secondary(Path("runs/codex-a0prime-test409"), args.a0prime_packets),
    }

    result = {
        "prereg": "docs/prereg/A6A.md v1.0 + Amendment 1 (freeze tag a6a-freeze-1)",
        "grading": "deterministic (build_labels rules) + 3-vote codex panel (single-family, conservative-lower-bound convention); failures scored 0",
        "n": primary["n"],
        "primary": primary,
        "strata_exploratory": strata,
        "secondary": secondary,
    }
    out = args.grading_dir / "final_result.json"
    out.write_text(json.dumps(result, indent=1) + "\n")

    p = primary
    print(f"PRIMARY (all 409, canonical): A6a {p['acc_a']:.1%} vs A0' {p['acc_b']:.1%}")
    print(f"  diff {p['diff']:+.1%} | discordant {p['discordant_a_only']} vs {p['discordant_b_only']} | McNemar p={p['mcnemar_p']:.2e}")
    cb = p["cluster_bootstrap"]
    print(f"  cluster bootstrap 95% CI [{cb['ci_low']:+.1%}, {cb['ci_high']:+.1%}] over {cb['n_clusters']} patients")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
