#!/usr/bin/env python3
"""Build a deterministic, whole-Patient C3/C3G holdout selection receipt.

This tool is zero-model infrastructure. It consumes private candidate metadata
and a keyed burned-history registry, selects whole Patient clusters, and emits
an aggregate public receipt. Raw selected identities belong in the private
output only and must never be staged on the solver host or committed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import stat
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = "c3g-holdout-selection-v1"
BURNED_SCHEMA_VERSION = "c3g-burned-registry-v1"
ALGORITHM_VERSION = "whole-patient-stratified-deficit-v1"
REQUIRED_FIELDS = (
    "question_id",
    "patient_fhir_id",
    "template_id",
    "main_table_name",
)


@dataclass(frozen=True)
class HoldoutSelection:
    private_rows: list[dict[str, str]]
    receipt: dict[str, object]


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def keyed_identity(key: bytes, namespace: str, value: str) -> str:
    return hmac.new(
        key,
        f"c3g-{namespace}-v1\0{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def load_key(path: Path) -> bytes:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode not in (0o400, 0o600):
        raise ValueError("holdout HMAC key must have mode 0400 or 0600")
    key = path.read_bytes()
    if len(key) < 32:
        raise ValueError("holdout HMAC key must contain at least 32 bytes")
    return key


def validate_rows(rows: Iterable[dict]) -> list[dict[str, str]]:
    validated: list[dict[str, str]] = []
    seen_questions: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"candidate row {index} is not an object")
        row: dict[str, str] = {}
        for field in REQUIRED_FIELDS:
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"candidate row {index} has invalid {field}")
            row[field] = value.strip()
        question = row["question_id"]
        if question in seen_questions:
            raise ValueError(f"duplicate question in candidate metadata: {question}")
        seen_questions.add(question)
        validated.append(row)
    if not validated:
        raise ValueError("candidate metadata is empty")
    return sorted(validated, key=lambda item: item["question_id"])


def _validate_registry(registry: dict, *, key_id: str) -> tuple[set[str], set[str]]:
    if registry.get("schema_version") != BURNED_SCHEMA_VERSION:
        raise ValueError("burned registry schema is invalid")
    if registry.get("key_id") != key_id:
        raise ValueError("burned registry key_id does not match selection key")
    questions = registry.get("question_hmacs")
    patients = registry.get("patient_hmacs")
    if not isinstance(questions, list) or not isinstance(patients, list):
        raise ValueError("burned registry identity lists are invalid")
    if any(not isinstance(value, str) or len(value) != 64 for value in questions):
        raise ValueError("burned question HMAC is invalid")
    if any(not isinstance(value, str) or len(value) != 64 for value in patients):
        raise ValueError("burned Patient HMAC is invalid")
    return set(questions), set(patients)


def _stratum(row: dict[str, str]) -> str:
    return f"{row['template_id']}|{row['main_table_name']}"


def _priority(seed: int, patient: str) -> str:
    return sha256(f"{ALGORITHM_VERSION}\0{seed}\0{patient}".encode("utf-8"))


def _select_patient_groups(
    groups: dict[str, list[dict[str, str]]],
    *,
    seed: int,
    target_questions: int,
    min_patients: int,
) -> list[str]:
    available_questions = sum(len(values) for values in groups.values())
    if available_questions < target_questions:
        raise ValueError("eligible corpus has fewer questions than the target")
    if len(groups) < min_patients:
        raise ValueError("eligible corpus has fewer Patient clusters than required")

    available_strata = Counter(
        _stratum(item) for values in groups.values() for item in values
    )
    desired = {
        stratum: target_questions * count / available_questions
        for stratum, count in available_strata.items()
    }
    current: Counter[str] = Counter()
    selected: list[str] = []
    remaining = set(groups)

    while (
        sum(len(groups[patient]) for patient in selected) < target_questions
        or len(selected) < min_patients
    ):
        if not remaining:
            raise ValueError("whole-Patient selection cannot satisfy the requested size")

        def rank(patient: str) -> tuple[float, int, str]:
            contribution = Counter(_stratum(item) for item in groups[patient])
            deficit_coverage = sum(
                min(float(count), max(0.0, desired[stratum] - current[stratum]))
                for stratum, count in contribution.items()
            )
            # Prefer deficit coverage, then smaller groups to limit overshoot,
            # then a seeded content hash for an input-order-independent tie break.
            return (-deficit_coverage, len(groups[patient]), _priority(seed, patient))

        chosen = min(remaining, key=rank)
        remaining.remove(chosen)
        selected.append(chosen)
        current.update(_stratum(item) for item in groups[chosen])

    return selected


def select_holdout(
    rows: Iterable[dict],
    *,
    burned_registry: dict,
    key: bytes,
    key_id: str,
    seed: int,
    target_questions: int,
    min_patients: int = 40,
) -> HoldoutSelection:
    if target_questions < 1:
        raise ValueError("target_questions must be positive")
    if min_patients < 40:
        raise ValueError("confirmatory holdout requires at least 40 Patient clusters")
    candidates = validate_rows(rows)
    burned_questions, burned_patients = _validate_registry(
        burned_registry, key_id=key_id
    )

    for item in candidates:
        if keyed_identity(key, "question", item["question_id"]) in burned_questions:
            raise ValueError("candidate metadata contains a burned question")
        if keyed_identity(key, "patient", item["patient_fhir_id"]) in burned_patients:
            raise ValueError("candidate metadata contains a burned Patient")

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in candidates:
        groups[item["patient_fhir_id"]].append(item)
    for values in groups.values():
        values.sort(key=lambda item: item["question_id"])

    selected_patients = _select_patient_groups(
        groups,
        seed=seed,
        target_questions=target_questions,
        min_patients=min_patients,
    )
    selected_rows = sorted(
        [item for patient in selected_patients for item in groups[patient]],
        key=lambda item: item["question_id"],
    )
    public_projection = [
        {
            "question_hmac": keyed_identity(key, "question", item["question_id"]),
            "patient_cluster_hmac": keyed_identity(
                key, "patient", item["patient_fhir_id"]
            ),
            "template": item["template_id"],
            "source_table": item["main_table_name"],
        }
        for item in selected_rows
    ]
    strata = Counter(_stratum(item) for item in selected_rows)
    candidate_projection = [
        [
            item["question_id"],
            item["patient_fhir_id"],
            item["template_id"],
            item["main_table_name"],
        ]
        for item in candidates
    ]
    receipt: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm": ALGORITHM_VERSION,
        "key_id": key_id,
        "seed": seed,
        "candidate_metadata_sha256": sha256(canonical_bytes(candidate_projection)),
        "burned_registry_sha256": sha256(canonical_bytes(burned_registry)),
        "selected_projection_sha256": sha256(canonical_bytes(public_projection)),
        "candidate_questions": len(candidates),
        "candidate_patient_clusters": len(groups),
        "target_questions": target_questions,
        "minimum_patient_clusters": min_patients,
        "selected_questions": len(selected_rows),
        "selected_patient_clusters": len(selected_patients),
        "whole_patient_selection": True,
        "zero_burned_overlap": True,
        "strata_counts": dict(sorted(strata.items())),
    }
    return HoldoutSelection(private_rows=selected_rows, receipt=receipt)


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-metadata", type=Path, required=True)
    parser.add_argument("--burned-registry", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--target-questions", type=int, required=True)
    parser.add_argument("--min-patients", type=int, default=40)
    parser.add_argument("--private-selection", type=Path, required=True)
    parser.add_argument("--public-receipt", type=Path, required=True)
    args = parser.parse_args()

    selection = select_holdout(
        _load_jsonl(args.candidate_metadata),
        burned_registry=json.loads(args.burned_registry.read_text(encoding="utf-8")),
        key=load_key(args.key_file),
        key_id=args.key_id,
        seed=args.seed,
        target_questions=args.target_questions,
        min_patients=args.min_patients,
    )
    args.private_selection.parent.mkdir(parents=True, exist_ok=True)
    args.public_receipt.parent.mkdir(parents=True, exist_ok=True)
    args.private_selection.write_text(
        "".join(
            json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
            for item in selection.private_rows
        ),
        encoding="utf-8",
    )
    args.private_selection.chmod(0o600)
    args.public_receipt.write_text(
        json.dumps(selection.receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(selection.receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
