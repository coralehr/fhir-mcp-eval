#!/usr/bin/env python3
"""Deterministic post-result sensitivity analysis for A11b raw answers.

The input adapter is intentionally separate from grading.  It consumes one
aggregate-safe row per arm/question and emits counts and paired statistics only;
it never writes answer text, question text, resource identifiers, or Patient
identifiers to its result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import paired_stats


ARMS = ("t0", "t1", "e1")
DEFAULT_ALPHA = 0.025
DEFAULT_BOOTSTRAPS = 10_000
DEFAULT_SEED = 20260716
RULE = "answer_prefix_insufficient_evidence_or_data_v1"


def explicit_insufficiency(answer: object) -> bool:
    if not isinstance(answer, str):
        return False
    normalized = answer.lstrip().casefold()
    return normalized.startswith(("insufficient evidence", "insufficient data"))


def _contrast(
    *,
    treatment: str,
    reference: str,
    question_ids: list[str],
    clusters: Mapping[str, str],
    labels: Mapping[str, Mapping[str, int]],
    n_boot: int,
    alpha: float,
    seed: int,
) -> dict[str, Any]:
    pairs = [
        (
            clusters[question_id],
            labels[treatment][question_id],
            labels[reference][question_id],
        )
        for question_id in question_ids
    ]
    summary = paired_stats.paired_summary(
        pairs,
        n_boot=n_boot,
        alpha=alpha,
        seed=seed,
    )
    return {
        "treatment": treatment,
        "reference": reference,
        "n": summary["n"],
        "treatment_accuracy": summary["acc_a"],
        "reference_accuracy": summary["acc_b"],
        "accuracy_difference": summary["diff"],
        "discordant_treatment_only": summary["discordant_a_only"],
        "discordant_reference_only": summary["discordant_b_only"],
        "exact_two_sided_mcnemar_p": summary["mcnemar_p"],
        "patient_cluster_bootstrap": summary["cluster_bootstrap"],
        "descriptive_95_patient_cluster_bootstrap": (
            paired_stats.cluster_bootstrap_ci(
                pairs,
                n_boot=n_boot,
                alpha=0.05,
                seed=seed,
            )
        ),
    }


def analyze(
    rows: Iterable[Mapping[str, Any]],
    *,
    n_boot: int = DEFAULT_BOOTSTRAPS,
    alpha: float = DEFAULT_ALPHA,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    by_host: dict[tuple[str, str], Mapping[str, Any]] = {}
    clusters: dict[str, str] = {}
    for row in rows:
        question_id = row.get("question_id")
        arm = row.get("arm")
        cluster = row.get("patient_cluster_sha256")
        answerable = row.get("answerable")
        strict_correct = row.get("strict_correct")
        if (
            not isinstance(question_id, str)
            or not question_id
            or arm not in ARMS
            or not isinstance(cluster, str)
            or not cluster
            or type(answerable) is not bool
            or type(strict_correct) is not bool
        ):
            raise ValueError("sensitivity row has invalid identity or label fields")
        host = (str(arm), question_id)
        if host in by_host:
            raise ValueError("sensitivity rows contain a duplicate arm/question")
        if question_id in clusters and clusters[question_id] != cluster:
            raise ValueError("question Patient cluster differs across arms")
        by_host[host] = row
        clusters[question_id] = cluster

    question_ids = sorted(clusters)
    expected = {(arm, question_id) for question_id in question_ids for arm in ARMS}
    if not question_ids or set(by_host) != expected:
        raise ValueError("sensitivity rows do not cover exact arm/question triplets")
    if len(set(clusters.values())) != len(question_ids):
        raise ValueError("sensitivity analysis requires one Patient per question")

    labels = {arm: {} for arm in ARMS}
    behavior = {
        arm: {
            "n": 0,
            "nonempty_reason": 0,
            "explicit_insufficiency": 0,
        }
        for arm in ARMS
    }
    for question_id in question_ids:
        answerability = {
            bool(by_host[(arm, question_id)]["answerable"]) for arm in ARMS
        }
        if len(answerability) != 1:
            raise ValueError("question answerability differs across arms")
        answerable = answerability.pop()
        for arm in ARMS:
            row = by_host[(arm, question_id)]
            if answerable:
                labels[arm][question_id] = int(row["strict_correct"])
                continue
            reason = row.get("raw_insufficiency_reason")
            explicit = explicit_insufficiency(row.get("raw_answer"))
            behavior[arm]["n"] += 1
            behavior[arm]["nonempty_reason"] += int(
                isinstance(reason, str) and bool(reason.strip())
            )
            behavior[arm]["explicit_insufficiency"] += int(explicit)
            labels[arm][question_id] = int(explicit)

    accuracy = {
        arm: {
            "n": len(question_ids),
            "correct": sum(labels[arm].values()),
            "accuracy": sum(labels[arm].values()) / len(question_ids),
        }
        for arm in ARMS
    }
    return {
        "schema_version": "a11b-forensic-sensitivity-v1",
        "registered": False,
        "confirmatory_use_prohibited": True,
        "semantic_rule": RULE,
        "bootstrap": {
            "alpha": alpha,
            "confidence": 1 - alpha,
            "replicates": n_boot,
            "seed": seed,
        },
        "questions": len(question_ids),
        "raw_unanswerable_behavior": behavior,
        "accuracy_by_arm": accuracy,
        "contrasts": {
            "t1_minus_t0": _contrast(
                treatment="t1",
                reference="t0",
                question_ids=question_ids,
                clusters=clusters,
                labels=labels,
                n_boot=n_boot,
                alpha=alpha,
                seed=seed,
            ),
            "e1_minus_t1": _contrast(
                treatment="e1",
                reference="t1",
                question_ids=question_ids,
                clusters=clusters,
                labels=labels,
                n_boot=n_boot,
                alpha=alpha,
                seed=seed,
            ),
        },
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"sensitivity input line {line_number} is not an object")
        rows.append(value)
    return rows


def analyze_r3_artifacts(
    *,
    controller_path: Path,
    bundle_path: Path,
    preview_root: Path,
    audit_root: Path,
) -> dict[str, Any]:
    """Replay the exact completed r3 artifacts without emitting answer content."""

    import a11b_grading
    import a11b_unregistered_postprocess as post
    import a11b_unregistered_preview as preview

    controller, _invocations = post._load_context(
        controller_path=controller_path,
        bundle_path=bundle_path,
        preview_root=preview_root,
    )
    grading_root = preview_root / post.GRADING_DIR
    panel_root = preview_root / post.PANEL_DIR
    coverage = post._read_object(grading_root / "completion_coverage.json")
    deterministic = post._read_object(grading_root / "deterministic_labels.json")
    queue = post._read_jsonl(grading_root / "panel_queue.jsonl")
    panel_verdicts = post._read_object(panel_root / "panel_verdicts.json")
    labels = a11b_grading.final_labels(
        question_ids=coverage["question_ids"],
        deterministic=deterministic,
        panel_queue=queue,
        panel_verdicts=panel_verdicts,
    )
    post._verify_audit_tree(
        audit_root, controller["inputs"]["audit_manifest_sha256"]
    )
    gold = {
        row["question_id"]: row
        for row in post._read_jsonl(audit_root / "efficacy/gold.jsonl")
    }
    rows = []
    selected_slot_receipts = []
    for index, host in enumerate(controller["schedule"]["items"]):
        slot_dir = preview_root / "slots" / f"{index:04d}"
        if not preview._accepted_marker_valid(
            slot_dir, index, str(host["prompt_sha256"])
        ):
            raise ValueError(f"invalid r3 acceptance marker at slot {index}")
        marker = post._read_object(slot_dir / "accepted.json")
        attempt_dir = slot_dir / f"attempt-{marker['attempt_number']}"
        raw_answer_path = attempt_dir / "answer.json"
        raw_answer = post._read_object(raw_answer_path)
        selected_slot_receipts.append(
            {
                "index": index,
                "accepted_marker_sha256": hashlib.sha256(
                    (slot_dir / "accepted.json").read_bytes()
                ).hexdigest(),
                "raw_answer_sha256": hashlib.sha256(
                    raw_answer_path.read_bytes()
                ).hexdigest(),
            }
        )
        question_id = str(host["question_id"])
        arm = str(host["arm"])
        gold_row = gold[question_id]
        rows.append(
            {
                "question_id": question_id,
                "patient_cluster_sha256": gold_row["patient_cluster_sha256"],
                "arm": arm,
                "answerable": gold_row["answerable"],
                "strict_correct": bool(labels[arm][question_id]),
                "raw_answer": raw_answer.get("answer"),
                "raw_insufficiency_reason": raw_answer.get(
                    "insufficiency_reason"
                ),
            }
        )
    result = analyze(rows)
    grading_receipts = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in {
            "completion_coverage.json": grading_root / "completion_coverage.json",
            "deterministic_labels.json": grading_root / "deterministic_labels.json",
            "panel_queue.jsonl": grading_root / "panel_queue.jsonl",
            "panel_verdicts.json": panel_root / "panel_verdicts.json",
        }.items()
    }
    preview_binding = {
        "selected_slots": selected_slot_receipts,
        "grading_artifacts": grading_receipts,
    }
    result["preview_input_receipt"] = {
        "schema_version": "a11b-forensic-preview-input-receipt-v1",
        "selected_slot_count": len(selected_slot_receipts),
        "grading_artifacts": grading_receipts,
        "root_sha256": hashlib.sha256(
            json.dumps(
                preview_binding,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
    }
    result["input_hashes"] = {
        "controller_sha256": hashlib.sha256(controller_path.read_bytes()).hexdigest(),
        "bundle_sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        "audit_manifest_sha256": hashlib.sha256(
            (audit_root / "manifest.json").read_bytes()
        ).hexdigest(),
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--controller", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--preview-root", type=Path)
    parser.add_argument("--audit-root", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.input is not None:
        result = analyze(_read_jsonl(args.input))
    else:
        if any(
            value is None
            for value in (args.bundle, args.preview_root, args.audit_root)
        ):
            parser.error(
                "--controller requires --bundle, --preview-root, and --audit-root"
            )
        result = analyze_r3_artifacts(
            controller_path=args.controller,
            bundle_path=args.bundle,
            preview_root=args.preview_root,
            audit_root=args.audit_root,
        )
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
