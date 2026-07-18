#!/usr/bin/env python3
"""Deterministically finalize the 64-patient A11b successor development gate."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import a11b_postprocess
import a11b_successor_dev_gate
import a11b_successor_development_grading
from a11_evidence_core import canonical_bytes


PROFILE = "a11b-successor-development-v1"
ANSWER_CALLS = 192


def _export_rows(
    *, controller: Mapping[str, Any], trusted_executor: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    exported = trusted_executor.export_completed_run()
    if (
        exported.get("run_id") != controller.get("run_id")
        or exported.get("schedule_length") != ANSWER_CALLS
        or exported.get("accepted_slots") != ANSWER_CALLS
    ):
        raise ValueError("successor executor export differs from controller")
    attempts = exported.get("attempts")
    if not isinstance(attempts, list):
        raise ValueError("successor executor attempt export changed")
    by_slot: dict[int, list[Mapping[str, Any]]] = {}
    for row in attempts:
        descriptor = row.get("descriptor") if isinstance(row, Mapping) else None
        index = descriptor.get("schedule_index") if isinstance(descriptor, Mapping) else None
        if type(index) is not int or not 0 <= index < ANSWER_CALLS:
            raise ValueError("successor executor schedule index changed")
        by_slot.setdefault(index, []).append(row)
    if set(by_slot) != set(range(ANSWER_CALLS)):
        raise ValueError("successor executor slot coverage is incomplete")

    accepted_answers: list[dict[str, Any]] = []
    all_attempts: list[dict[str, Any]] = []
    for index, host in enumerate(controller["schedule"]["items"]):
        if (
            host.get("schedule_index") != index
            or host.get("arm") not in a11b_successor_development_grading.ARMS
            or not isinstance(host.get("question_id"), str)
        ):
            raise ValueError("successor host schedule changed")
        slot = sorted(
            by_slot[index], key=lambda row: row["descriptor"]["attempt_number"]
        )
        for row in slot:
            all_attempts.append(
                {
                    "question_id": host["question_id"],
                    "arm": host["arm"],
                    "attempt_number": row["descriptor"]["attempt_number"],
                    "outcome": row["outcome"],
                    "token_usage": row["token_usage"],
                }
            )
        accepted = slot[-1]
        if accepted.get("outcome") != "accepted":
            raise ValueError("successor completed slot is not accepted")
        _artifact, files = a11b_postprocess._decode_artifact(accepted)
        answer = json.loads(files["answer.json"])
        if not isinstance(answer, dict):
            raise ValueError("successor accepted answer is invalid")
        accepted_answers.append(
            {
                "question_id": host["question_id"],
                "arm": host["arm"],
                "answer": answer,
                "token_usage": accepted["token_usage"],
            }
        )
    return accepted_answers, all_attempts


def run_all(
    *,
    bundle_root: Path,
    audit_root: Path,
    trusted_executor: Any,
    controller: Mapping[str, Any],
    controller_sha256: str,
) -> dict[str, Any]:
    """Open gold only after exact completion and publish the zero-model gate."""

    if controller.get("experiment_profile") != PROFILE:
        raise ValueError("successor postprocessor received the wrong profile")
    accepted_answers, all_attempts = _export_rows(
        controller=controller,
        trusted_executor=trusted_executor,
    )
    audit_manifest = a11b_postprocess._verify_audit_tree(
        audit_root,
        controller["inputs"]["audit_manifest_sha256"],
    )
    gold_rows = a11b_postprocess._read_jsonl(
        audit_root / "development/gold.jsonl"
    )
    result = a11b_successor_development_grading.compile_result(
        gold_rows=gold_rows,
        accepted_answers=accepted_answers,
        all_attempts=all_attempts,
        audit_manifest_sha256=controller["inputs"]["audit_manifest_sha256"],
    )
    gate = a11b_successor_dev_gate.compile_gate_receipt(
        assignments=result["assignments"],
        outcomes=result["outcomes"],
        development_result_manifest=result["manifest"],
    )
    output = Path(controller["outputs"]["result"])
    if output != bundle_root / "results/final":
        raise ValueError("successor result path changed")
    manifest = {
        "schema_version": "a11b-successor-development-final-v1",
        "controller_manifest_sha256": controller_sha256,
        "audit_manifest_sha256": controller["inputs"]["audit_manifest_sha256"],
        "audit_artifact_count": len(audit_manifest["artifacts"]),
        "development_result_manifest_sha256": gate[
            "development_result_manifest_sha256"
        ],
        "promotion": (
            "development_gate_passed"
            if gate["status"] == "passed"
            else "development_gate_failed"
        ),
        "model_calls": 0,
    }
    artifacts = {
        "grading.json": canonical_bytes(result) + b"\n",
        "gate.json": canonical_bytes(gate) + b"\n",
        "manifest.json": canonical_bytes(manifest) + b"\n",
    }
    a11b_postprocess._publish_directory(output, artifacts)
    return manifest
