#!/usr/bin/env python3
"""Assemble the A6a-vs-A0' primary confirmatory result (prereg §2, §6, §7).

Combines deterministic verdicts + panel majority verdicts into per-arm labels
(failures already scored 0 by the deterministic pass), then emits the
pre-registered paired analysis plus the exploratory strata and secondary
metrics. By default this uses the full input; an explicit schedule restricts
every metric to that question set.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import codex_harness
from grade_a6a_confirmatory import gold_type
from paired_stats import paired_summary
from question_selection import load_scheduled_question_ids, select_question_rows

ARMS = ("a6a", "a0prime")


def packet_char_counts(packet_path: Path, question_ids: list[str]) -> list[int]:
    """Return packet sizes for exactly the scheduled questions."""
    requested = set(question_ids)
    counts: dict[str, int] = {}
    for line in packet_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        question_id = str(record.get("question_id"))
        if question_id not in requested:
            continue
        if question_id in counts:
            raise ValueError(f"duplicate packet for scheduled question_id {question_id}: {packet_path}")
        packet = record.get("packet") or {}
        counts[question_id] = int((packet.get("bounds") or {}).get("char_count", 0))
    missing = [question_id for question_id in question_ids if question_id not in counts]
    if missing:
        preview = ", ".join(missing[:10])
        suffix = "..." if len(missing) > 10 else ""
        raise ValueError(
            f"packet file is missing {len(missing)} scheduled question_id(s): {preview}{suffix} ({packet_path})"
        )
    return [counts[question_id] for question_id in question_ids]


def arm_secondary(run_dir: Path, packets: Path, question_ids: list[str]) -> dict:
    """Compute answer and packet economics on one explicit question set."""
    abstentions = 0
    answered = 0
    for question_id in question_ids:
        question_dir = run_dir / "questions" / question_id
        answer_path = question_dir / "answer.json"
        if codex_harness.terminal_question_status(question_dir) == "contaminated":
            continue
        if not answer_path.exists():
            continue
        if codex_harness.audit_event_log(question_dir / "events.jsonl")[
            "contaminated"
        ]:
            continue
        answered += 1
        if json.loads(answer_path.read_text(encoding="utf-8")).get("insufficiency_reason"):
            abstentions += 1
    chars = sorted(packet_char_counts(packets, question_ids))
    return {
        "answered": answered,
        "abstentions": abstentions,
        "packet_chars_median": chars[len(chars) // 2],
        "packet_chars_total": sum(chars),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grading-dir", type=Path, default=Path("runs/a6a-confirmatory-grading"))
    ap.add_argument("--input", type=Path, default=Path("final_dataset/full_test409.csv"))
    ap.add_argument("--a6a-packets", type=Path, default=Path("runs/a6a_test409_packets.jsonl"))
    ap.add_argument("--a0prime-packets", type=Path, default=Path("runs/a0prime_test409_packets.jsonl"))
    ap.add_argument("--a6a-dir", type=Path, default=Path("runs/codex-a6a-test409"))
    ap.add_argument("--a0prime-dir", type=Path, default=Path("runs/codex-a0prime-test409"))
    ap.add_argument("--question-spec", type=Path, default=None, help="JSON list or object containing scheduled question_ids")
    ap.add_argument("--question-id", action="append", default=[], help="restrict assembly to this scheduled question ID (repeatable)")
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

    # secondary metrics: abstentions + packet economics, restricted to the
    # same scheduled IDs as the primary/bootstrap/strata analysis.
    try:
        secondary = {
            "a6a": arm_secondary(args.a6a_dir, args.a6a_packets, qids),
            "a0prime": arm_secondary(args.a0prime_dir, args.a0prime_packets, qids),
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        ap.error(str(exc))

    result = {
        "prereg": "docs/prereg/A6A.md v1.0 + Amendment 1 (freeze tag a6a-freeze-1)",
        "grading": "deterministic (build_labels rules) + 3-vote codex panel (single-family, conservative-lower-bound convention); failures scored 0",
        "n": primary["n"],
        "primary": primary,
        "strata_exploratory": strata,
        "secondary": secondary,
    }
    if scheduled_ids is not None:
        result["question_selection"] = {
            "explicit": True,
            "question_ids": qids,
        }
    out = args.grading_dir / "final_result.json"
    out.write_text(json.dumps(result, indent=1) + "\n")

    p = primary
    scope = f"scheduled n={len(qids)}" if scheduled_ids is not None else f"all {len(qids)}, canonical"
    print(f"PRIMARY ({scope}): A6a {p['acc_a']:.1%} vs A0' {p['acc_b']:.1%}")
    print(f"  diff {p['diff']:+.1%} | discordant {p['discordant_a_only']} vs {p['discordant_b_only']} | McNemar p={p['mcnemar_p']:.2e}")
    cb = p["cluster_bootstrap"]
    print(f"  cluster bootstrap 95% CI [{cb['ci_low']:+.1%}, {cb['ci_high']:+.1%}] over {cb['n_clusters']} patients")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
