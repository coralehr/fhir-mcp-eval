#!/usr/bin/env python3
"""Build the patient-disjoint, zero-model A11b T0/T1/E1 corpus."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import hmac
import io
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from a11_answer_harness import (
    PROMPT_RECORD_VERSION,
    build_verified_prompt,
    render_prompt_bytes,
)
from a11_evidence_core import canonical_bytes, resource_ref, sha256
from a11b_event_compiler import (
    ARM_E1,
    ARM_T0,
    ARM_T1,
    compile_arms,
    plan_question,
)
from a11b_generation_receipt import (
    _load_json,
    _load_json_bytes,
    assign_patient_ids,
    verify_generation_receipt,
)


CORPUS_VERSION = "a11b-corpus-v1"
PUBLIC_MANIFEST_VERSION = "a11b-public-corpus-manifest-v1"
AUDIT_MANIFEST_VERSION = "a11b-audit-corpus-manifest-v1"
MAX_PACKET_BYTES = 512_000
NOISE_RESOURCES = 64
ARM_ARTIFACTS = {
    "t0": ARM_T0,
    "t1": ARM_T1,
    "e1": ARM_E1,
}


@dataclass(frozen=True)
class Family:
    name: str
    root_type: str
    first_relation: str
    terminal_relation: str
    terminal_type: str


FAMILIES = (
    Family(
        "observation_finding",
        "Observation",
        "Observation.hasMember",
        "Observation.hasMember",
        "Observation",
    ),
    Family(
        "observation_specimen",
        "Observation",
        "Observation.hasMember",
        "Observation.specimen",
        "Specimen",
    ),
    Family(
        "diagnostic_finding",
        "DiagnosticReport",
        "DiagnosticReport.result",
        "Observation.hasMember",
        "Observation",
    ),
    Family(
        "diagnostic_specimen",
        "DiagnosticReport",
        "DiagnosticReport.result",
        "Observation.specimen",
        "Specimen",
    ),
)

ANSWERABLE_DIFFICULTIES = tuple(
    [f"unique_instant_{index}" for index in range(6)]
    + [f"timezone_shifted_{index}" for index in range(3)]
    + [f"period_endpoint_{index}" for index in range(3)]
    + [f"nonselected_tie_{index}" for index in range(3)]
    + [f"nonselected_path_incomplete_{index}" for index in range(3)]
)
UNANSWERABLE_DIFFICULTIES = (
    "selected_path_incomplete",
    "temporal_tie",
    "precision_ambiguous",
    "clinical_time_missing",
    "conflicting_effective_fields",
    "temporal_overlap",
)
DIFFICULTY_SCHEDULE = ANSWERABLE_DIFFICULTIES + UNANSWERABLE_DIFFICULTIES


def _opaque(key: bytes, *parts: str, length: int = 24) -> str:
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("A11b nonce key must be exactly 32 bytes")
    message = b"\x00".join(part.encode("utf-8") for part in parts)
    return hmac.new(key, b"a11b-corpus-v1\x00" + message, hashlib.sha256).hexdigest()[
        :length
    ]


def derive_corpus_key(
    *, generation_receipt_sha256: str, power_receipt_sha256: str
) -> bytes:
    """Derive the sole corpus-construction key from public sealed receipts."""

    values = (generation_receipt_sha256, power_receipt_sha256)
    if any(
        len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in values
    ):
        raise ValueError("A11b corpus key inputs must be lowercase SHA-256 digests")
    return hashlib.sha256(
        b"a11b-public-corpus-key-v1\x00"
        + generation_receipt_sha256.encode("ascii")
        + b"\x00"
        + power_receipt_sha256.encode("ascii")
    ).digest()


def _bundle_resources(bundle: object) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise ValueError("source patient artifact is not a FHIR Bundle")
    entries = bundle.get("entry")
    if not isinstance(entries, list):
        raise ValueError("source FHIR Bundle entry is invalid")
    resources = [
        entry.get("resource")
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("resource"), dict)
    ]
    patients = [resource for resource in resources if resource.get("resourceType") == "Patient"]
    if len(patients) != 1 or not isinstance(patients[0].get("id"), str):
        raise ValueError("source FHIR Bundle must contain exactly one Patient")
    return copy.deepcopy(patients[0]), [
        copy.deepcopy(resource)
        for resource in resources
        if resource.get("resourceType") != "Patient"
    ]


def _resource(
    resource_type: str,
    identifier: str,
    patient_ref: str,
    **fields: Any,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "resourceType": resource_type,
        "id": identifier,
        "meta": {"versionId": "1"},
        "subject": {"reference": patient_ref},
    }
    value.update(fields)
    return value


def _fact(
    key: bytes,
    patient_id: str,
    public_patient_id: str,
    split: str,
    cell: int,
    occurrence: int,
    branch: int,
    terminal_type: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    token = _opaque(
        key,
        patient_id,
        split,
        str(cell),
        str(occurrence),
        str(branch),
        "fact",
        length=10,
    ).upper()
    identifier = _opaque(
        key,
        patient_id,
        split,
        str(cell),
        str(occurrence),
        str(branch),
        "terminal",
    )
    patient_ref = f"Patient/{public_patient_id}"
    if terminal_type == "Observation":
        answer = {"code": f"O-{token}", "display": f"Synthetic organism {token}"}
        return (
            _resource(
                "Observation",
                identifier,
                patient_ref,
                status="final",
                code={"text": "Microbiology organism finding"},
                valueCodeableConcept={
                    "coding": [
                        {
                            "system": "https://example.invalid/a11b-organism",
                            **answer,
                        }
                    ]
                },
            ),
            answer,
        )
    answer = {"code": f"S-{token}", "display": f"Synthetic specimen {token}"}
    return (
        _resource(
            "Specimen",
            identifier,
            patient_ref,
            status="available",
            type={
                "coding": [
                    {
                        "system": "https://example.invalid/a11b-specimen",
                        **answer,
                    }
                ],
                "text": answer["display"],
            },
        ),
        answer,
    )


def _question_text(
    question_id: str,
    family: Family,
    depth: int,
    temporal_policy: str,
) -> str:
    root = "DiagnosticReport" if family.root_type == "DiagnosticReport" else "Observation"
    target = "organism was found" if family.terminal_type == "Observation" else "specimen was used"
    intermediate = " through an intermediate observation" if depth == 3 else ""
    return (
        f"For synthetic record {question_id}, what {target} in the "
        f"{temporal_policy} microbiology culture {root}{intermediate}?"
    )


def _relation_pointer(relation: str) -> str:
    field = relation.split(".", 1)[1]
    return f"/{field}" if relation == "Observation.specimen" else f"/{field}/0"


def _append_reference(
    source: dict[str, Any],
    relation: str,
    target: dict[str, Any] | None,
) -> None:
    field = relation.split(".", 1)[1]
    reference = (
        {"reference": resource_ref(target)}
        if target is not None
        else {"display": "Reference withheld"}
    )
    if relation == "Observation.specimen":
        source[field] = reference
    else:
        source.setdefault(field, []).append(reference)


def _event_fields(
    difficulty: str,
    temporal_policy: str,
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = [
        {"effectiveDateTime": "2100-01-01T12:00:00Z"},
        {"effectiveDateTime": "2100-01-02T12:00:00Z"},
        {"effectiveDateTime": "2100-01-03T12:00:00Z"},
    ]
    selected = 0 if temporal_policy == "first" else 2
    if difficulty.startswith("timezone_shifted"):
        values = [
            {"effectiveDateTime": "2100-01-01T07:00:00-05:00"},
            {"effectiveDateTime": "2100-01-02T13:00:00+01:00"},
            {"effectiveDateTime": "2100-01-03T21:00:00+09:00"},
        ]
    elif difficulty.startswith("period_endpoint"):
        values = [
            {
                "effectivePeriod": {
                    "start": f"2100-01-0{index + 1}T08:00:00Z",
                    "end": f"2100-01-0{index + 1}T16:00:00Z",
                }
            }
            for index in range(3)
        ]
    elif difficulty.startswith("nonselected_tie"):
        if temporal_policy == "first":
            values[1] = {"effectiveDateTime": "2100-01-03T12:00:00Z"}
        else:
            values[1] = {"effectiveDateTime": "2100-01-01T12:00:00Z"}
    elif difficulty == "temporal_tie":
        peer = 1
        values[peer] = copy.deepcopy(values[selected])
    elif difficulty == "precision_ambiguous":
        if temporal_policy == "first":
            values[0] = {"effectiveDateTime": "2100-01-01"}
            values[1] = {"effectiveDateTime": "2100-01-01T12:00:00Z"}
        else:
            values[1] = {"effectiveDateTime": "2100-01-03T12:00:00Z"}
            values[2] = {"effectiveDateTime": "2100-01-03"}
    elif difficulty == "clinical_time_missing":
        values[selected] = {}
    elif difficulty == "conflicting_effective_fields":
        values[selected]["effectivePeriod"] = {
            "start": "2100-01-01T08:00:00Z",
            "end": "2100-01-01T16:00:00Z",
        }
    elif difficulty == "temporal_overlap":
        if temporal_policy == "first":
            values[0] = {
                "effectivePeriod": {
                    "start": "2100-01-01T08:00:00Z",
                    "end": "2100-01-01T12:00:00Z",
                }
            }
            values[1] = {
                "effectivePeriod": {
                    "start": "2100-01-01T08:00:00Z",
                    "end": "2100-01-02T12:00:00Z",
                }
            }
        else:
            values[1] = {
                "effectivePeriod": {
                    "start": "2100-01-02T08:00:00Z",
                    "end": "2100-01-03T12:00:00Z",
                }
            }
            values[2] = {
                "effectivePeriod": {
                    "start": "2100-01-03T08:00:00Z",
                    "end": "2100-01-03T12:00:00Z",
                }
            }
    return values


def _safe_noise(
    patient_id: str,
    public_patient_id: str,
    resources: list[dict[str, Any]],
    *,
    nonce_key: bytes,
) -> list[dict[str, Any]]:
    selected = sorted(
        resources,
        key=lambda resource: (
            _opaque(
                nonce_key,
                patient_id,
                str(resource.get("resourceType")),
                str(resource.get("id")),
                "noise",
                length=64,
            ),
            str(resource.get("resourceType")),
            str(resource.get("id")),
        ),
    )[:NOISE_RESOURCES]
    result = []
    for resource in selected:
        resource_type = resource.get("resourceType")
        identifier = resource.get("id")
        if not isinstance(resource_type, str) or not isinstance(identifier, str):
            continue
        value: dict[str, Any] = {
            "resourceType": resource_type,
            "id": _opaque(
                nonce_key,
                patient_id,
                resource_type,
                identifier,
                "noise-public-id",
            ),
            "meta": {"versionId": str(resource.get("meta", {}).get("versionId", "1"))},
            "subject": {"reference": f"Patient/{public_patient_id}"},
        }
        for field in ("status", "code", "clinicalStatus", "category"):
            if field in resource:
                value[field] = copy.deepcopy(resource[field])
        result.append(value)
    return result


def _schedule(split: str, cell_index: int, occurrence: int) -> tuple[str, str]:
    if split == "efficacy":
        if not 0 <= occurrence < 48:
            raise ValueError("efficacy cell occurrence is out of range")
        difficulty = DIFFICULTY_SCHEDULE[occurrence % len(DIFFICULTY_SCHEDULE)]
        temporal_policy = "first" if occurrence < 24 else "latest"
        return difficulty, temporal_policy
    if split == "development":
        if not 0 <= occurrence < 8:
            raise ValueError("development cell occurrence is out of range")
        difficulty = DIFFICULTY_SCHEDULE[(cell_index * 3 + occurrence) % 24]
        temporal_policy = "first" if (cell_index + occurrence) % 2 == 0 else "latest"
        return difficulty, temporal_policy
    raise ValueError("A11b split is invalid")


def build_case(
    patient_bundle: object,
    *,
    split: str,
    family_index: int,
    occurrence: int,
    nonce_key: bytes,
) -> dict[str, Any]:
    """Build and compile one blind three-event A11b case."""

    if not 0 <= family_index < 8:
        raise ValueError("family-depth cell is invalid")
    family = FAMILIES[family_index // 2]
    depth = 2 + (family_index % 2)
    patient, source_noise = _bundle_resources(patient_bundle)
    patient_id = patient["id"]
    public_patient_id = _opaque(
        nonce_key,
        patient_id,
        split,
        str(family_index),
        str(occurrence),
        "patient-public-id",
    )
    patient_ref = f"Patient/{public_patient_id}"
    difficulty, temporal_policy = _schedule(split, family_index, occurrence)
    question_id = f"a11bq-{_opaque(nonce_key, patient_id, split, str(family_index), str(occurrence))}"
    question_text = _question_text(
        question_id,
        family,
        depth,
        temporal_policy,
    )
    question_plan = plan_question(question_text)
    selected_index = 0 if temporal_policy == "first" else 2
    event_fields = _event_fields(difficulty, temporal_policy)
    roots: list[dict[str, Any]] = []
    for branch in range(3):
        roots.append(
            _resource(
                family.root_type,
                _opaque(
                    nonce_key,
                    patient_id,
                    split,
                    str(family_index),
                    str(occurrence),
                    str(branch),
                    "event",
                ),
                patient_ref,
                status="final",
                code={"text": "Microbiology culture event"},
                **event_fields[branch],
            )
        )

    relevant_resources: list[dict[str, Any]] = list(roots)
    citations: list[dict[str, Any]] = []
    facts: list[dict[str, str]] = []
    incomplete_branch: int | None = None
    if difficulty == "selected_path_incomplete":
        incomplete_branch = selected_index
    elif difficulty.startswith("nonselected_path_incomplete"):
        incomplete_branch = 1 if selected_index != 1 else 0
    for branch, root in enumerate(roots):
        terminal, fact = _fact(
            nonce_key,
            patient_id,
            public_patient_id,
            split,
            family_index,
            occurrence,
            branch,
            family.terminal_type,
        )
        facts.append(fact)
        current = root
        steps: list[dict[str, Any]] = []
        for bridge_index in range(depth - 1):
            bridge = _resource(
                "Observation",
                _opaque(
                    nonce_key,
                    patient_id,
                    split,
                    str(family_index),
                    str(occurrence),
                    str(branch),
                    str(bridge_index),
                    "bridge",
                ),
                patient_ref,
                status="final",
                code={"text": "Microbiology panel"},
            )
            relation = family.first_relation if bridge_index == 0 else "Observation.hasMember"
            _append_reference(current, relation, bridge)
            steps.append(
                {
                    "source": resource_ref(current),
                    "json_pointer": _relation_pointer(relation),
                    "target_type": "Observation",
                    "target": resource_ref(bridge),
                }
            )
            relevant_resources.append(bridge)
            current = bridge
        available = branch != incomplete_branch
        _append_reference(current, family.terminal_relation, terminal if available else None)
        steps.append(
            {
                "source": resource_ref(current),
                "json_pointer": _relation_pointer(family.terminal_relation),
                "target_type": family.terminal_type,
                "target": resource_ref(terminal) if available else None,
            }
        )
        if available:
            relevant_resources.append(terminal)
        citations.append(
            {
                "state": "available" if available else "unavailable",
                "steps": steps,
                "target": resource_ref(terminal) if available else None,
                "target_type": family.terminal_type,
            }
        )

    patient_summary: dict[str, Any] = {
        "resourceType": "Patient",
        "id": public_patient_id,
        "meta": {"versionId": str(patient.get("meta", {}).get("versionId", "1"))},
    }
    noise = _safe_noise(
        patient_id,
        public_patient_id,
        source_noise,
        nonce_key=nonce_key,
    )
    seen: set[str] = set()
    resources = []
    for resource in [patient_summary, *noise, *relevant_resources]:
        reference = resource_ref(resource)
        if reference in seen:
            continue
        seen.add(reference)
        resources.append(resource)
    source = {
        "resources": resources,
        "path_citations": citations,
        "root_refs": sorted(resource_ref(root) for root in roots),
        "bounds": {"outcomes": []},
    }
    compiled = compile_arms(
        source,
        question_text,
        question_plan,
        max_packet_bytes=MAX_PACKET_BYTES,
    )
    aids = compiled["arms"][ARM_T1]["temporal_aids"]
    answerable = difficulty in ANSWERABLE_DIFFICULTIES
    expected_state = "sufficient" if answerable else "insufficient"
    if aids["answerability_receipt"]["state"] != expected_state:
        raise ValueError("compiled answerability differs from the registered stratum")
    expected_reason = None if answerable else difficulty
    actual_reason = aids["answerability_receipt"]["reason"]
    if expected_reason is not None and actual_reason != expected_reason:
        raise ValueError(
            f"compiled failure reason differs: expected={expected_reason}, actual={actual_reason}"
        )
    question = {
        "question_id": question_id,
        "question": question_text,
        "assumption": "All records and identifiers are synthetic and non-PHI.",
    }
    gold = {
        "question_id": question_id,
        "patient_cluster_sha256": hmac.new(
            nonce_key,
            b"a11b-patient-cluster-v1\x00" + patient_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest(),
        "answerable": answerable,
        "failure_mode": None if answerable else difficulty,
        "reference_answer": facts[selected_index] if answerable else None,
        "selected_root_ref": (
            resource_ref(roots[selected_index])
            if answerable or difficulty == "selected_path_incomplete"
            else None
        ),
        "selected_path_refs": (
            sorted(
                {
                    reference
                    for step in citations[selected_index]["steps"]
                    for reference in (step.get("source"), step.get("target"))
                    if isinstance(reference, str)
                }
            )
            if answerable
            else []
        ),
        "path_signature": question_plan["path_signatures"][0],
    }
    audit = {
        "question_id": question_id,
        "family": family.name,
        "depth": depth,
        "temporal_policy": temporal_policy,
        "difficulty": difficulty,
        "root_count": len(roots),
        "noise_resource_count": len(noise),
        "equivalence_receipt": compiled["equivalence_receipt"],
    }
    return {
        "source": source,
        "question": question,
        "gold": gold,
        "audit": audit,
        "compiled": compiled,
        "patient_id": patient_id,
    }


def _prompt_record(question: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    payload_bytes = canonical_bytes(payload)
    payload_text = payload_bytes.decode("utf-8")
    prompt_bytes = render_prompt_bytes(question, payload_text)
    record = {
        "schema_version": PROMPT_RECORD_VERSION,
        "question_id": question["question_id"],
        "model_payload_json": payload_text,
        "model_payload_sha256": sha256(payload_bytes),
        "model_payload_utf8_bytes": len(payload_bytes),
        "prompt_text": prompt_bytes.decode("utf-8"),
        "prompt_sha256": sha256(prompt_bytes),
    }
    build_verified_prompt(question, record)
    return record


def construct_corpus(
    patient_bundles: list[dict[str, Any]],
    *,
    power_receipt: dict[str, Any],
    nonce_key: bytes,
) -> dict[str, Any]:
    """Construct both patient-disjoint splits entirely in memory."""

    by_patient: dict[str, dict[str, Any]] = {}
    for bundle in patient_bundles:
        patient, _ = _bundle_resources(bundle)
        patient_id = patient["id"]
        if patient_id in by_patient:
            raise ValueError("source generation contains a duplicate Patient")
        by_patient[patient_id] = bundle
    development_ids, efficacy_ids = assign_patient_ids(by_patient, power_receipt)

    def build_split(split: str, patient_ids: list[str]) -> dict[str, Any]:
        questions: list[dict[str, str]] = []
        gold: list[dict[str, Any]] = []
        audit: list[dict[str, Any]] = []
        packets: dict[str, list[dict[str, Any]]] = {
            artifact: [] for artifact in ARM_ARTIFACTS
        }
        for position, patient_id in enumerate(patient_ids):
            cell_index = position % 8
            occurrence = position // 8
            case = build_case(
                by_patient[patient_id],
                split=split,
                family_index=cell_index,
                occurrence=occurrence,
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
            "questions": questions,
            "packets": packets,
            "gold": gold,
            "audit": audit,
            "patient_ids": list(patient_ids),
        }

    return {
        "schema_version": CORPUS_VERSION,
        "corpus_derivation_key_sha256": sha256(nonce_key),
        "development": build_split("development", development_ids),
        "efficacy": build_split("efficacy", efficacy_ids),
        "model_calls": 0,
    }


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_bytes(row) + b"\n" for row in rows)


def _answer_input_csv(rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=["question_id", "question", "assumption"],
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _artifact_receipt(payload: bytes) -> dict[str, Any]:
    return {"sha256": sha256(payload), "bytes": len(payload)}


def _write_exclusive_tree(root: Path, files: dict[str, bytes]) -> None:
    root = Path(os.path.abspath(root))
    if root.exists() or root.is_symlink():
        raise ValueError(f"output root already exists: {root}")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        for relative in sorted(files):
            path = root / relative
            if path.parent != root:
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            try:
                payload = files[relative]
                written = 0
                while written < len(payload):
                    written += os.write(descriptor, payload[written:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except BaseException:
        # Leave partial output in place for forensic inspection. A replay must use
        # a new root and can never mistake this directory for a complete corpus.
        raise


def _manifest_files(files: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    return {name: _artifact_receipt(payload) for name, payload in sorted(files.items())}


def _assert_public_blind(files: dict[str, bytes]) -> None:
    forbidden = {
        b'"answerable"',
        b'"failure_mode"',
        b'"gold"',
        b'"reference_answer"',
        b'"selected_root_ref"',
        b'"patient_id"',
    }
    for name, payload in files.items():
        lowered = payload.lower()
        for marker in forbidden:
            if marker in lowered:
                raise ValueError(f"public corpus leaks audit-only field in {name}")


def materialize_corpus(
    corpus: dict[str, Any],
    *,
    public_root: Path,
    audit_root: Path,
    generation_spec_sha256: str,
    generation_receipt_sha256: str,
    power_receipt_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write physically separate public and auditor-only corpus trees."""

    public_root = Path(os.path.abspath(public_root))
    audit_root = Path(os.path.abspath(audit_root))
    if public_root == audit_root or public_root in audit_root.parents or audit_root in public_root.parents:
        raise ValueError("public and audit roots must be physically separate")
    if corpus.get("schema_version") != CORPUS_VERSION or corpus.get("model_calls") != 0:
        raise ValueError("in-memory corpus contract changed")

    public_files: dict[str, bytes] = {}
    audit_files: dict[str, bytes] = {}
    for split in ("development", "efficacy"):
        value = corpus.get(split)
        if not isinstance(value, dict):
            raise ValueError(f"missing A11b split: {split}")
        public_files[f"{split}/answer_input.csv"] = _answer_input_csv(value["questions"])
        for artifact in ARM_ARTIFACTS:
            public_files[f"{split}/{artifact}_packets.jsonl"] = _jsonl(
                value["packets"][artifact]
            )
        audit_files[f"{split}/gold.jsonl"] = _jsonl(value["gold"])
        audit_files[f"{split}/audit.jsonl"] = _jsonl(value["audit"])
    _assert_public_blind(public_files)

    common = {
        "corpus_version": CORPUS_VERSION,
        "corpus_derivation_key_sha256": corpus["corpus_derivation_key_sha256"],
        "generation_spec_sha256": generation_spec_sha256,
        "generation_receipt_sha256": generation_receipt_sha256,
        "power_receipt_sha256": power_receipt_sha256,
        "split_counts": {"development": 64, "efficacy": 384},
        "arms": list(ARM_ARTIFACTS),
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


def load_sanitized_patient_bundles(
    artifact_root: Path,
    *,
    nonce_key: bytes,
) -> list[dict[str, Any]]:
    """Stream large Synthea bundles into a bounded non-PHI corpus substrate."""

    output = artifact_root / "output"
    paths = sorted(output.rglob("*.json"))
    if len(paths) != 448:
        raise ValueError("verified generation output must contain exactly 448 files")
    bundles: list[dict[str, Any]] = []
    for path in paths:
        raw = path.read_bytes()
        value = _load_json_bytes(raw, path.name)
        patient, resources = _bundle_resources(value)
        patient_id = patient["id"]
        noise = _safe_noise(
            patient_id,
            patient_id,
            resources,
            nonce_key=nonce_key,
        )
        bundles.append(
            {
                "resourceType": "Bundle",
                "type": "collection",
                "entry": [
                    {"resource": patient},
                    *({"resource": resource} for resource in noise),
                ],
            }
        )
    return bundles


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
    spec = _load_json(generation_spec_path)
    receipt = _load_json(generation_receipt_path)
    power_spec = _load_json(power_spec_path)
    power_receipt = _load_json(power_receipt_path)
    verify_generation_receipt(
        spec,
        receipt,
        artifact_root=artifact_root,
        power_spec=power_spec,
        power_receipt=power_receipt,
    )
    generation_receipt_sha256 = sha256(generation_receipt_path.read_bytes())
    power_receipt_sha256 = sha256(power_receipt_path.read_bytes())
    nonce_key = derive_corpus_key(
        generation_receipt_sha256=generation_receipt_sha256,
        power_receipt_sha256=power_receipt_sha256,
    )
    bundles = load_sanitized_patient_bundles(artifact_root, nonce_key=nonce_key)
    # Close the read window: the source tree must still equal the sealed receipt
    # after corpus ingestion, not merely immediately before it.
    verify_generation_receipt(
        spec,
        receipt,
        artifact_root=artifact_root,
        power_spec=power_spec,
        power_receipt=power_receipt,
    )
    corpus = construct_corpus(
        bundles,
        power_receipt=power_receipt,
        nonce_key=nonce_key,
    )
    return materialize_corpus(
        corpus,
        public_root=public_root,
        audit_root=audit_root,
        generation_spec_sha256=receipt["generation_spec_sha256"],
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
                "audit_manifest_sha256": sha256(canonical_bytes(audit) + b"\n"),
                "model_calls": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
