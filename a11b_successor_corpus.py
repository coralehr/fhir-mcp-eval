#!/usr/bin/env python3
"""Build the fresh A11b successor development corpus without opening efficacy."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path
from typing import Any

import a11_answer_harness
import a11b_answer_contract
from a11_evidence_core import canonical_bytes, sha256
from a11b_corpus_builder import (
    ARM_ARTIFACTS,
    _answer_input_csv,
    _assert_public_blind,
    _jsonl,
    _manifest_files,
    _write_exclusive_tree,
    build_case,
    derive_corpus_key,
    load_sanitized_patient_bundles,
)
from a11b_generation_receipt import (
    _load_json,
    _load_json_bytes,
    assign_patient_ids,
    verify_generation_receipt,
)


CORPUS_VERSION = "a11b-successor-development-corpus-v1"
PUBLIC_MANIFEST_VERSION = "a11b-successor-development-public-manifest-v1"
AUDIT_MANIFEST_VERSION = "a11b-successor-development-audit-manifest-v1"
DEVELOPMENT_PATIENT_COUNT = 64
RESERVED_EFFICACY_PATIENT_COUNT = 384
SPENT_GENERATION_RECEIPT_SHA256 = (
    "246d9dc82e27c237629099a01305e9ca65fa4ed49c1beb253803c08c57bc601a"
)
SPENT_RAW_OUTPUT_CONTENT_SHA256 = (
    "273e83b72ecd3a5069ea8d10975ec3bffcc16d9b083995fd321e1a7fe2cfc3d2"
)
REGISTERED_GENERATION_SPEC_FILE_SHA256 = (
    "2c4ecf7ea44f42452799576da7b0d0814ddf933cbb054311d555d56bc20261d4"
)
REGISTERED_GENERATION_RECEIPT_FILE_SHA256 = (
    "acb5ad3ba2ba8032507d69afc8375d181dc49376392e39564343490f718df0d8"
)
REGISTERED_GENERATION_SPEC_SHA256 = (
    "b3ddad494ad18160be57657133cf69fce1d3fbd2c6f504f5dec9e41a6b5c6c97"
)
REGISTERED_RAW_OUTPUT_CONTENT_SHA256 = (
    "57830fe09242d215fac90a7ccdebba24188af290f11290ce1f9cb66a99ab27b4"
)
REGISTERED_PATIENT_MANIFEST_SHA256 = (
    "76c3ffba9f4d2703e4e24b754ac850db9852f04637b38f0717de836a590c8844"
)
REGISTERED_PARTITION_MANIFEST_SHA256 = (
    "ac08e626576706d53a5c28cbaca02df1c14b50d820968d61680327701531e3eb"
)
MAX_CONTROL_JSON_BYTES = 2 * 1024 * 1024


def _read_control_json(path: Path) -> tuple[bytes, Any]:
    """Read one unique regular control file once and bind bytes to parsing."""

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError(f"cannot safely open control JSON: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > MAX_CONTROL_JSON_BYTES
        ):
            raise ValueError(f"invalid control JSON file: {path.name}")
        data = bytearray()
        while chunk := os.read(descriptor, 1024 * 1024):
            data.extend(chunk)
            if len(data) > MAX_CONTROL_JSON_BYTES:
                raise ValueError(f"control JSON exceeds byte bound: {path.name}")
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if before_identity != after_identity or len(data) != before.st_size:
            raise ValueError(f"control JSON changed during read: {path.name}")
    finally:
        os.close(descriptor)
    payload = bytes(data)
    return payload, _load_json_bytes(payload, path.name)


def assert_registered_generation(
    *,
    spec_bytes: bytes,
    receipt_bytes: bytes,
    receipt: dict[str, Any],
) -> None:
    """Require the exact independently reproduced successor source identity."""

    raw_output = receipt.get("raw_output")
    population = receipt.get("source_population")
    assignment = population.get("assignment") if isinstance(population, dict) else None
    if (
        sha256(spec_bytes) != REGISTERED_GENERATION_SPEC_FILE_SHA256
        or sha256(receipt_bytes) != REGISTERED_GENERATION_RECEIPT_FILE_SHA256
        or receipt.get("generation_spec_sha256")
        != REGISTERED_GENERATION_SPEC_SHA256
        or not isinstance(raw_output, dict)
        or raw_output.get("content_sha256")
        != REGISTERED_RAW_OUTPUT_CONTENT_SHA256
        or not isinstance(population, dict)
        or population.get("patient_manifest_sha256")
        != REGISTERED_PATIENT_MANIFEST_SHA256
        or not isinstance(assignment, dict)
        or assignment.get("partition_manifest_sha256")
        != REGISTERED_PARTITION_MANIFEST_SHA256
    ):
        raise ValueError("registered successor generation identity changed")


def assert_fresh_generation(
    receipt: dict[str, Any], *, generation_receipt_sha256: str
) -> None:
    """Reject either exact identity of the spent r3 source generation."""

    raw_output = receipt.get("raw_output")
    content_sha256 = (
        raw_output.get("content_sha256")
        if isinstance(raw_output, dict)
        else None
    )
    if (
        generation_receipt_sha256 == SPENT_GENERATION_RECEIPT_SHA256
        or content_sha256 == SPENT_RAW_OUTPUT_CONTENT_SHA256
    ):
        raise ValueError("spent A11b r3 generation cannot be reopened")


def _patient_id(bundle: dict[str, Any]) -> str:
    patients = [
        entry.get("resource")
        for entry in bundle.get("entry", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("resource"), dict)
        and entry["resource"].get("resourceType") == "Patient"
    ]
    if len(patients) != 1 or not isinstance(patients[0].get("id"), str):
        raise ValueError("source bundle must contain exactly one Patient")
    return str(patients[0]["id"])


def _prompt_record(
    question: dict[str, str], payload: dict[str, Any]
) -> dict[str, Any]:
    record = a11_answer_harness.make_successor_prompt_record(
        question,
        canonical_bytes(payload).decode("utf-8"),
    )
    a11_answer_harness.build_verified_successor_prompt(question, record)
    return record


def construct_development_corpus(
    patient_bundles: list[dict[str, Any]],
    *,
    power_receipt: dict[str, Any],
    nonce_key: bytes,
) -> dict[str, Any]:
    """Construct only development; reserve efficacy identities without packets."""

    by_patient: dict[str, dict[str, Any]] = {}
    for bundle in patient_bundles:
        patient_id = _patient_id(bundle)
        if patient_id in by_patient:
            raise ValueError("source generation contains a duplicate Patient")
        by_patient[patient_id] = bundle
    development_ids, efficacy_ids = assign_patient_ids(by_patient, power_receipt)
    questions: list[dict[str, str]] = []
    gold: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    packets: dict[str, list[dict[str, Any]]] = {
        artifact: [] for artifact in ARM_ARTIFACTS
    }
    for position, patient_id in enumerate(development_ids):
        case = build_case(
            by_patient[patient_id],
            split="development",
            family_index=position % 8,
            occurrence=position // 8,
            nonce_key=nonce_key,
        )
        questions.append(case["question"])
        gold.append(case["gold"])
        audit.append(case["audit"])
        for artifact, arm in ARM_ARTIFACTS.items():
            packets[artifact].append(
                _prompt_record(case["question"], case["compiled"]["arms"][arm])
            )
    return {
        "schema_version": CORPUS_VERSION,
        "corpus_derivation_key_sha256": sha256(nonce_key),
        "development": {
            "questions": questions,
            "packets": packets,
            "gold": gold,
            "audit": audit,
        },
        "reserved_efficacy_patient_count": len(efficacy_ids),
        "model_calls": 0,
    }


def _ordered_question_ids(
    rows: object,
    *,
    label: str,
    exact_fields: frozenset[str] | None = None,
) -> list[str]:
    if not isinstance(rows, list) or len(rows) != DEVELOPMENT_PATIENT_COUNT:
        raise ValueError(f"development corpus {label} count changed")
    identifiers: list[str] = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or (exact_fields is not None and set(row) != exact_fields)
            or not isinstance(row.get("question_id"), str)
            or not row["question_id"]
        ):
            raise ValueError(f"development corpus {label} shape changed")
        identifiers.append(row["question_id"])
    if len(set(identifiers)) != DEVELOPMENT_PATIENT_COUNT:
        raise ValueError(f"development corpus {label} identities changed")
    return identifiers


def _validate_development(corpus: dict[str, Any]) -> dict[str, Any]:
    development = corpus.get("development")
    if (
        not isinstance(development, dict)
        or set(development) != {"questions", "packets", "gold", "audit"}
        or corpus.get("reserved_efficacy_patient_count")
        != RESERVED_EFFICACY_PATIENT_COUNT
    ):
        raise ValueError("development corpus shape changed")
    question_ids = _ordered_question_ids(
        development["questions"],
        label="question",
        exact_fields=a11_answer_harness.INPUT_FIELDS,
    )
    if _ordered_question_ids(development["gold"], label="gold") != question_ids:
        raise ValueError("development corpus gold identities changed")
    if _ordered_question_ids(development["audit"], label="audit") != question_ids:
        raise ValueError("development corpus audit identities changed")
    packets = development["packets"]
    if not isinstance(packets, dict) or set(packets) != set(ARM_ARTIFACTS):
        raise ValueError("development corpus packet arms changed")
    rows_by_id = dict(zip(question_ids, development["questions"], strict=True))
    for artifact in ARM_ARTIFACTS:
        records = packets[artifact]
        if (
            _ordered_question_ids(records, label=f"{artifact} packet")
            != question_ids
        ):
            raise ValueError("development corpus packet identities changed")
        for record in records:
            a11_answer_harness.build_verified_successor_prompt(
                rows_by_id[record["question_id"]],
                record,
            )
    return development


def _canonical_output_root(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    parent = absolute.parent
    try:
        resolved_parent = parent.resolve(strict=True)
        metadata = resolved_parent.lstat()
    except OSError as exc:
        raise ValueError("output parent must be a real directory") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("output parent must be a real directory")
    return resolved_parent / absolute.name


def materialize_development_corpus(
    corpus: dict[str, Any],
    *,
    public_root: Path,
    audit_root: Path,
    generation_spec_sha256: str,
    generation_receipt_sha256: str,
    power_receipt_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write only development artifacts to physically separate trees."""

    if corpus.get("schema_version") != CORPUS_VERSION:
        raise ValueError("successor corpus contract changed")
    if corpus.get("model_calls") != 0 or "efficacy" in corpus:
        raise ValueError("development build cannot contain efficacy artifacts")
    development = _validate_development(corpus)
    public_root = _canonical_output_root(public_root)
    audit_root = _canonical_output_root(audit_root)
    if (
        public_root == audit_root
        or public_root in audit_root.parents
        or audit_root in public_root.parents
    ):
        raise ValueError("public and audit roots must be physically separate")

    public_files = {
        "development/answer_input.csv": _answer_input_csv(
            development["questions"]
        ),
        **{
            f"development/{artifact}_packets.jsonl": _jsonl(
                development["packets"][artifact]
            )
            for artifact in ARM_ARTIFACTS
        },
    }
    audit_files = {
        "development/gold.jsonl": _jsonl(development["gold"]),
        "development/audit.jsonl": _jsonl(development["audit"]),
    }
    _assert_public_blind(public_files)
    common = {
        "corpus_version": CORPUS_VERSION,
        "corpus_derivation_key_sha256": corpus[
            "corpus_derivation_key_sha256"
        ],
        "generation_spec_sha256": generation_spec_sha256,
        "generation_receipt_sha256": generation_receipt_sha256,
        "power_receipt_sha256": power_receipt_sha256,
        "split_counts": {"development": len(development["questions"])},
        "reserved_efficacy_patient_count": corpus[
            "reserved_efficacy_patient_count"
        ],
        "efficacy_materialized": False,
        "arms": list(ARM_ARTIFACTS),
        "answer_contract_version": a11b_answer_contract.CONTRACT_VERSION,
        "prompt_record_version": a11_answer_harness.SUCCESSOR_PROMPT_RECORD_VERSION,
        "model_calls": 0,
    }
    public_manifest = {
        "schema_version": PUBLIC_MANIFEST_VERSION,
        **common,
        "artifacts": _manifest_files(public_files),
        "contains_gold": False,
        "contains_raw_patient_identifiers": False,
        "contains_synthetic_fhir_ids": True,
    }
    public_files["manifest.json"] = canonical_bytes(public_manifest) + b"\n"
    public_files["manifest.sha256"] = (
        sha256(public_files["manifest.json"]) + "\n"
    ).encode("ascii")
    audit_manifest = {
        "schema_version": AUDIT_MANIFEST_VERSION,
        **common,
        "public_manifest_sha256": sha256(public_files["manifest.json"]),
        "artifacts": _manifest_files(audit_files),
        "patient_clusters_are_keyed": True,
        "raw_patient_identifiers_disclosed": False,
    }
    audit_files["manifest.json"] = canonical_bytes(audit_manifest) + b"\n"
    audit_files["manifest.sha256"] = (
        sha256(audit_files["manifest.json"]) + "\n"
    ).encode("ascii")
    _write_exclusive_tree(public_root, public_files)
    _write_exclusive_tree(audit_root, audit_files)
    return public_manifest, audit_manifest


def build_from_generation(
    *,
    artifact_root: Path,
    generation_spec_path: Path,
    generation_receipt_path: Path,
    power_spec_path: Path,
    power_receipt_path: Path,
    public_root: Path,
    audit_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify one fresh source and materialize only its development split."""

    spec_bytes, spec = _read_control_json(generation_spec_path)
    receipt_bytes, receipt = _read_control_json(generation_receipt_path)
    power_spec = _load_json(power_spec_path)
    power_receipt = _load_json(power_receipt_path)
    if not isinstance(receipt, dict) or not isinstance(power_receipt, dict):
        raise ValueError("successor receipt inputs must be JSON objects")
    assert_registered_generation(
        spec_bytes=spec_bytes,
        receipt_bytes=receipt_bytes,
        receipt=receipt,
    )
    verify_generation_receipt(
        spec,
        receipt,
        artifact_root=artifact_root,
        power_spec=power_spec,
        power_receipt=power_receipt,
    )
    assert_registered_generation(
        spec_bytes=spec_bytes,
        receipt_bytes=receipt_bytes,
        receipt=receipt,
    )
    generation_receipt_sha256 = sha256(receipt_bytes)
    assert_fresh_generation(
        receipt,
        generation_receipt_sha256=generation_receipt_sha256,
    )
    power_receipt_sha256 = sha256(power_receipt_path.read_bytes())
    nonce_key = derive_corpus_key(
        generation_receipt_sha256=generation_receipt_sha256,
        power_receipt_sha256=power_receipt_sha256,
    )
    bundles = load_sanitized_patient_bundles(
        artifact_root,
        nonce_key=nonce_key,
    )
    verify_generation_receipt(
        spec,
        receipt,
        artifact_root=artifact_root,
        power_spec=power_spec,
        power_receipt=power_receipt,
    )
    corpus = construct_development_corpus(
        bundles,
        power_receipt=power_receipt,
        nonce_key=nonce_key,
    )
    return materialize_development_corpus(
        corpus,
        public_root=public_root,
        audit_root=audit_root,
        generation_spec_sha256=str(receipt["generation_spec_sha256"]),
        generation_receipt_sha256=generation_receipt_sha256,
        power_receipt_sha256=power_receipt_sha256,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--generation-spec", type=Path, required=True)
    parser.add_argument("--generation-receipt", type=Path, required=True)
    parser.add_argument("--power-spec", type=Path, required=True)
    parser.add_argument("--power-receipt", type=Path, required=True)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    args = parser.parse_args()
    public, audit = build_from_generation(
        artifact_root=args.artifact_root,
        generation_spec_path=args.generation_spec,
        generation_receipt_path=args.generation_receipt,
        power_spec_path=args.power_spec,
        power_receipt_path=args.power_receipt,
        public_root=args.public_root,
        audit_root=args.audit_root,
    )
    print(
        json.dumps(
            {
                "public_manifest_sha256": sha256(
                    canonical_bytes(public) + b"\n"
                ),
                "audit_manifest_sha256": sha256(
                    canonical_bytes(audit) + b"\n"
                ),
                "efficacy_materialized": False,
                "model_calls": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
