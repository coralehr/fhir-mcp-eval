#!/usr/bin/env python3
"""Fail-closed zero-model launch preflight for the corrected C3/C3G study."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SCHEMA_VERSION = "c3g-sealed-bundle-v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CONFIRMATORY_ARMS = frozenset({"G0", "C1", "C2", "C3", "C3G", "RAW"})
REQUIRED_RECEIPTS = (
    "holdout_selection",
    "burned_registry",
    "gold_custody",
    "source_snapshot",
    "source_parity",
    "power",
    "solver_isolation",
    "judge_calibration",
    "treatment_parity",
    "schedule",
    "pricing",
    "analysis",
    "graph_compiler",
    "bundle_integrity",
    "independent_review",
)
COMMON_FIELDS = (
    "answer_model",
    "answer_model_family",
    "reasoning_effort",
    "base_prompt_sha256",
    "search_craft_sha256",
    "semantic_empty_retry",
    "model_round_budget",
    "fhir_request_budget",
    "fetcher_sha256",
    "answer_schema_sha256",
    "truncation_policy_sha256",
    "timeout_policy_sha256",
    "operational_retry_policy_sha256",
)


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _validate_common(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("common_answer_contract is missing")
    missing = sorted(set(COMMON_FIELDS) - set(value))
    if missing:
        raise ValueError("common_answer_contract missing: " + ", ".join(missing))
    for field in COMMON_FIELDS:
        item = value[field]
        if field.endswith("_sha256"):
            _require_sha256(item, field)
        elif field in {
            "semantic_empty_retry",
            "model_round_budget",
            "fhir_request_budget",
        }:
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                raise ValueError(f"{field} must be a nonnegative integer")
        elif not isinstance(item, str) or not item:
            raise ValueError(f"{field} must be a non-empty string")
    if value["semantic_empty_retry"] != 1:
        raise ValueError("C3/C3G require exactly one semantic-empty recovery")
    return value


def _validate_arms(value: object) -> None:
    if not isinstance(value, dict) or set(value) != CONFIRMATORY_ARMS:
        raise ValueError("bundle must define exactly G0/C1/C2/C3/C3G/RAW")
    for arm, delta in value.items():
        if not isinstance(delta, dict):
            raise ValueError(f"arm {arm} delta must be an object")
        forbidden = sorted(set(delta) - {"graph_packet"})
        if forbidden:
            raise ValueError(
                f"forbidden arm-specific fields in {arm}: " + ", ".join(forbidden)
            )
    if value["C3"]["graph_packet"] is not None:
        raise ValueError("C3 must not receive a graph packet")
    graph = value["C3G"]["graph_packet"]
    if not isinstance(graph, dict):
        raise ValueError("C3G requires a deterministic graph packet")
    if set(graph) != {"compiler_sha256", "config_sha256", "packet_cap_tokens"}:
        raise ValueError("C3G graph packet fields are invalid")
    _require_sha256(graph["compiler_sha256"], "C3G compiler_sha256")
    _require_sha256(graph["config_sha256"], "C3G config_sha256")
    if (
        not isinstance(graph["packet_cap_tokens"], int)
        or isinstance(graph["packet_cap_tokens"], bool)
        or graph["packet_cap_tokens"] < 1
    ):
        raise ValueError("C3G packet_cap_tokens must be positive")
    for arm in CONFIRMATORY_ARMS - {"C3G"}:
        if value[arm]["graph_packet"] is not None:
            raise ValueError(f"{arm} must not receive the C3G graph packet")


def _receipt_gate(receipts: object, name: str) -> bool:
    if not isinstance(receipts, dict):
        return False
    value = receipts.get(name)
    if not isinstance(value, dict):
        return False
    if value.get("status") != "pass":
        return False
    path = value.get("path")
    digest = value.get("sha256")
    return (
        isinstance(path, str)
        and bool(path)
        and isinstance(digest, str)
        and SHA256.fullmatch(digest) is not None
    )


def audit_bundle(bundle: dict) -> dict:
    if not isinstance(bundle, dict):
        raise ValueError("bundle manifest must be an object")
    if bundle.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("bundle schema is invalid")
    if bundle.get("replicates") != 3:
        raise ValueError("confirmatory schedule requires exactly three replicates")
    holdout = bundle.get("holdout")
    if not isinstance(holdout, dict):
        raise ValueError("holdout summary is missing")
    questions = holdout.get("questions")
    patients = holdout.get("patient_clusters")
    if not isinstance(questions, int) or isinstance(questions, bool) or questions < 1:
        raise ValueError("holdout questions must be positive")
    if not isinstance(patients, int) or isinstance(patients, bool) or patients < 40:
        raise ValueError("holdout must contain at least 40 Patient clusters")

    _validate_common(bundle.get("common_answer_contract"))
    _validate_arms(bundle.get("arms"))

    failed = [
        f"receipt:{name}"
        for name in REQUIRED_RECEIPTS
        if not _receipt_gate(bundle.get("receipts"), name)
    ]
    if bundle.get("state") != "SEALED":
        failed.insert(0, "state:SEALED")
    return {
        "schema_version": "c3g-preflight-report-v1",
        "launch_ready": not failed,
        "failed_gates": failed,
        "confirmatory_arms": sorted(CONFIRMATORY_ARMS),
        "replicates": 3,
        "questions": questions,
        "patient_clusters": patients,
        "treatment_contrast": "C3G_minus_C3",
        "only_permitted_delta": "deterministic_graph_packet",
    }


def assert_launch_ready(bundle: dict) -> dict:
    report = audit_bundle(bundle)
    if not report["launch_ready"]:
        raise ValueError(
            "C3G bundle is not launch-ready: " + ", ".join(report["failed_gates"])
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--assert-ready", action="store_true")
    args = parser.parse_args()
    bundle = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = assert_launch_ready(bundle) if args.assert_ready else audit_bundle(bundle)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["launch_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
