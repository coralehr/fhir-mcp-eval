#!/usr/bin/env python3
"""Build and verify the deterministic, zero-model A11 efficacy corpus."""

from __future__ import annotations

import argparse
import collections
import copy
import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import a6_packet_builder as a6
from a11_evidence_core import (
    REGISTERED_REFERENCE_PATHS,
    canonical_bytes,
    iter_explicit_references,
    project_star,
    project_traversal,
    resource_ref,
    sha256,
)
from a11_event_group_benchmark import (
    A11_DEPTH_AWARE_QUESTION_PLANNER_VERSION,
    A11_NORMALIZED_EVENT_RANK_VERSION,
    compile_event_groups,
    plan_question,
    rank_event_roots,
)


DATASET_VERSION = "a11-dataset-v1"
PROVENANCE_VERSION = "a11-source-provenance-v1"
AUGMENTATION_SEED = "a11-four-family-depth-aware-2026-07-15"
SOURCE_EPOCH = "2026-07-15-pre-answer"
REQUIRED_SOURCE_PATIENTS = 115
DEVELOPMENT_QUESTIONS = 24
EFFICACY_QUESTIONS = 120
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
MAX_TOTAL_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
PRACTICE_ID = "synthetic-practice-a11"
DENIED_PRACTICE_ID = "synthetic-practice-denied"
PRINCIPAL_ID = "synthetic-principal-a11"
PURPOSE = "TREATMENT"
ALLOWED_TYPES = ["DiagnosticReport", "Observation", "Specimen"]
FAILURE_MODES = ("missing", "stale_version", "out_of_scope", "bound_exhaustion")
OFFICIAL_SAMPLE = {
    "repository": "synthetichealth/synthea-sample-data",
    "repository_commit": "0d9dc0b56534cacb36db31c84e390ae936d03653",
    "generator_repository": "synthetichealth/synthea",
    "generator_commit": "2b0a55bab0ab9ae22204320c80f5880ceb8925aa",
    "artifact_path": "downloads/latest/synthea_sample_data_fhir_latest.zip",
    "git_blob_sha1": "e0d5f1f46a08bc0b373f7bc211b87dc2319572c9",
    "archive_sha256": "d32f10f98ec36bc6784bfe5f4e112d4850a6d0cb5dda6b9d8ca18fff5fb4a1d1",
    "archive_bytes": 44_578_263,
}
FORBIDDEN_SOURCE_KEYS = frozenset(
    {
        "answer",
        "answerable",
        "expected_answer",
        "expected_event_root",
        "expected_evidence_refs",
        "failure_mode",
        "gold",
        "gold_answer",
        "label",
        "reference_answer",
        "terminal_ref",
        "true_answer",
    }
)
FORBIDDEN_SOURCE_PREFIXES = ("expected_", "gold_", "true_")
FORBIDDEN_ID_FRAGMENTS = (
    "answer",
    "bound",
    "failure",
    "family",
    "gold",
    "latest",
    "missing",
    "root",
    "specimen",
    "stale",
    "terminal",
)
DEPENDENCY_FILES = (
    "a11_dataset_builder.py",
    "a11_evidence_core.py",
    "a11_event_group_benchmark.py",
    "a6_packet_builder.py",
)


def _registered_relation(
    source_type: str, field: str, target_type: str, shape: str
) -> str:
    relation = (source_type, field, target_type, shape)
    if relation not in REGISTERED_REFERENCE_PATHS:
        raise RuntimeError(f"unregistered A11 relation: {relation}")
    return f"{source_type}.{field}"


OBS_MEMBER = _registered_relation(
    "Observation", "hasMember", "Observation", "repeating"
)
OBS_SPECIMEN = _registered_relation(
    "Observation", "specimen", "Specimen", "singular"
)
DR_RESULT = _registered_relation(
    "DiagnosticReport", "result", "Observation", "repeating"
)


@dataclass(frozen=True)
class Family:
    family_id: str
    root_type: str
    first_relation: str
    terminal_relation: str
    terminal_type: str

    def signature(self, depth: int) -> list[str]:
        if depth == 2:
            return [self.first_relation, self.terminal_relation]
        if depth == 3:
            return [self.first_relation, OBS_MEMBER, self.terminal_relation]
        raise ValueError(f"unsupported A11 path depth: {depth}")


FAMILIES = (
    Family("observation_finding", "Observation", OBS_MEMBER, OBS_MEMBER, "Observation"),
    Family("observation_specimen", "Observation", OBS_MEMBER, OBS_SPECIMEN, "Specimen"),
    Family("diagnostic_finding", "DiagnosticReport", DR_RESULT, OBS_MEMBER, "Observation"),
    Family("diagnostic_specimen", "DiagnosticReport", DR_RESULT, OBS_SPECIMEN, "Specimen"),
)
FAMILY_BY_ID = {family.family_id: family for family in FAMILIES}
FROZEN_PROFILE = {
    "dataset_version": DATASET_VERSION,
    "augmentation_seed": AUGMENTATION_SEED,
    "development_questions": DEVELOPMENT_QUESTIONS,
    "efficacy_questions": EFFICACY_QUESTIONS,
    "families": [family.family_id for family in FAMILIES],
    "depths": [2, 3],
    "efficacy_questions_per_family_depth_cell": 15,
    "development_questions_per_family_depth_cell": 3,
    "efficacy_unanswerable_per_family_depth_cell": 3,
    "max_depth": 3,
    "max_targets": 8,
    "max_packet_bytes": 1_000_000,
}
FROZEN_PROFILE_SHA256 = sha256(canonical_bytes(FROZEN_PROFILE))


def _pretty(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _jsonl(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(canonical_bytes(row) + b"\n" for row in rows)


def _question_csv(rows: list[dict[str, Any]]) -> bytes:
    handle = io.StringIO(newline="")
    fields = ("question_id", "split", "question", "assumption", "patient_fhir_id")
    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row[field] for field in fields})
    return handle.getvalue().encode("utf-8")


def _policy_context(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "a11-policy-context-v1",
        "question_id": source["case_id"],
        "source_epoch": SOURCE_EPOCH,
        "source_case_sha256": sha256(canonical_bytes(source)),
        "patient_ref": source["patient_ref"],
        "principal": copy.deepcopy(source["principal"]),
        "allowed_purposes": list(source["allowed_purposes"]),
    }


def _is_hash(value: Any, length: int) -> bool:
    return isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{length}}}", value) is not None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key}")
        value[key] = child
    return value


def _loads(data: bytes, location: str) -> Any:
    try:
        return json.loads(data, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {location}") from exc


def _opaque(*parts: str, length: int = 24) -> str:
    digest = hashlib.sha256("\x00".join((AUGMENTATION_SEED, *parts)).encode()).hexdigest()
    return digest[:length]


def _check_safe_member(name: str) -> None:
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or "\\" in name
        or "\x00" in name
    ):
        raise ValueError(f"unsafe archive member path: {name}")


def inspect_source(input_path: Path) -> tuple[dict[str, Any], list[tuple[str, bytes]]]:
    """Return a logical, path-stable receipt and JSON payloads without extraction."""

    input_path = input_path.resolve()
    payloads: list[tuple[str, bytes]] = []
    if input_path.is_file():
        archive = input_path.read_bytes()
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise ValueError("source archive exceeds byte bound")
        if not zipfile.is_zipfile(input_path):
            raise ValueError("source file must be a ZIP archive")
        seen: set[str] = set()
        receipts: list[dict[str, Any]] = []
        total = 0
        with zipfile.ZipFile(input_path) as handle:
            infos = sorted(handle.infolist(), key=lambda info: info.filename)
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise ValueError("source archive exceeds entry bound")
            for info in infos:
                if info.is_dir():
                    continue
                _check_safe_member(info.filename)
                if info.filename in seen:
                    raise ValueError(f"duplicate archive member: {info.filename}")
                seen.add(info.filename)
                if info.flag_bits & 0x1:
                    raise ValueError("encrypted source archive entries are forbidden")
                if info.file_size > MAX_ENTRY_BYTES:
                    raise ValueError(f"source entry exceeds byte bound: {info.filename}")
                total += info.file_size
                if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise ValueError("source archive exceeds uncompressed byte bound")
                data = handle.read(info)
                if len(data) != info.file_size:
                    raise ValueError(f"source entry byte count mismatch: {info.filename}")
                receipt = {
                    "path": info.filename,
                    "sha256": sha256(data),
                    "bytes": len(data),
                }
                receipts.append(receipt)
                if info.filename.lower().endswith(".json"):
                    payloads.append((info.filename, data))
        if not payloads:
            raise ValueError("source archive contains no JSON entries")
        receipt = {
            "kind": "zip",
            "logical_path": input_path.name,
            "sha256": sha256(archive),
            "bytes": len(archive),
            "entries": receipts,
            "entry_manifest_sha256": sha256(canonical_bytes(receipts)),
            "content_sha256": _content_hash(payloads),
        }
        return receipt, payloads

    if not input_path.is_dir():
        raise ValueError("source input does not exist")
    receipts = []
    for path in sorted(input_path.rglob("*.json")):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"source directory contains an unsafe JSON path: {path.name}")
        relative = path.relative_to(input_path).as_posix()
        data = path.read_bytes()
        if len(data) > MAX_ENTRY_BYTES:
            raise ValueError(f"source entry exceeds byte bound: {relative}")
        receipts.append({"path": relative, "sha256": sha256(data), "bytes": len(data)})
        payloads.append((relative, data))
    if not payloads:
        raise ValueError("source directory contains no JSON files")
    receipt = {
        "kind": "directory",
        "logical_path": input_path.name,
        "entries": receipts,
        "entry_manifest_sha256": sha256(canonical_bytes(receipts)),
        "content_sha256": _content_hash(payloads),
    }
    receipt["sha256"] = sha256(canonical_bytes(receipts))
    receipt["bytes"] = sum(item["bytes"] for item in receipts)
    return receipt, payloads


def _content_hash(payloads: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for logical_path, data in sorted(payloads):
        encoded = logical_path.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _validate_provenance(provenance: dict[str, Any], source: dict[str, Any]) -> None:
    common = {
        "schema_version",
        "source_kind",
        "augmentation_seed",
        "augmentation_config_sha256",
        "entry_manifest_sha256",
        "content_sha256",
    }
    kind = provenance.get("source_kind")
    if kind == "official_sample_archive":
        expected = common | {
            "repository",
            "repository_commit",
            "generator_repository",
            "generator_commit",
            "artifact_path",
            "git_blob_sha1",
            "archive_sha256",
            "archive_bytes",
        }
    elif kind == "release_generation":
        expected = common | {
            "release_tag",
            "generator_commit",
            "jar_sha256",
            "seed",
            "population",
            "configuration_sha256",
            "raw_output_sha256",
        }
    else:
        raise ValueError("unsupported source_kind")
    if set(provenance) != expected:
        raise ValueError("source provenance fields changed")
    if provenance.get("schema_version") != PROVENANCE_VERSION:
        raise ValueError("unsupported source provenance schema")
    if provenance.get("augmentation_seed") != AUGMENTATION_SEED:
        raise ValueError("augmentation seed changed")
    if provenance.get("augmentation_config_sha256") != FROZEN_PROFILE_SHA256:
        raise ValueError("augmentation configuration changed")
    if provenance.get("entry_manifest_sha256") != source["entry_manifest_sha256"]:
        raise ValueError("source entry manifest changed")
    if provenance.get("content_sha256") != source["content_sha256"]:
        raise ValueError("source content hash changed")
    if kind == "official_sample_archive":
        if source["kind"] != "zip":
            raise ValueError("official sample source must be a ZIP archive")
        if provenance.get("repository") != "synthetichealth/synthea-sample-data":
            raise ValueError("official sample repository changed")
        if provenance.get("generator_repository") != "synthetichealth/synthea":
            raise ValueError("official generator repository changed")
        for field in ("repository_commit", "generator_commit", "git_blob_sha1"):
            if not _is_hash(provenance.get(field), 40):
                raise ValueError(f"invalid provenance {field}")
        if provenance.get("archive_sha256") != source["sha256"]:
            raise ValueError("source archive sha256 changed")
        if provenance.get("archive_bytes") != source["bytes"]:
            raise ValueError("source archive byte count changed")
        if not isinstance(provenance.get("artifact_path"), str) or not provenance["artifact_path"]:
            raise ValueError("source artifact path is missing")
        for field, expected_value in OFFICIAL_SAMPLE.items():
            if provenance.get(field) != expected_value:
                raise ValueError(f"preregistered official sample {field} changed")
    else:
        if not isinstance(provenance.get("release_tag"), str) or not provenance["release_tag"]:
            raise ValueError("source release tag is missing")
        if not _is_hash(provenance.get("generator_commit"), 40):
            raise ValueError("invalid generator commit")
        for field in ("jar_sha256", "configuration_sha256", "raw_output_sha256"):
            if not _is_hash(provenance.get(field), 64):
                raise ValueError(f"invalid provenance {field}")
        if provenance["raw_output_sha256"] != source["content_sha256"]:
            raise ValueError("raw output content hash changed")
        if not isinstance(provenance.get("seed"), int) or isinstance(provenance["seed"], bool):
            raise ValueError("source seed must be an integer")
        if (
            not isinstance(provenance.get("population"), int)
            or provenance["population"] != REQUIRED_SOURCE_PATIENTS
        ):
            raise ValueError("source population must equal the frozen 115-patient profile")


def official_sample_provenance(source: dict[str, Any]) -> dict[str, Any]:
    """Create the strict receipt only for the preregistered official archive."""

    if source.get("kind") != "zip":
        raise ValueError("official sample source must be a ZIP archive")
    if source.get("sha256") != OFFICIAL_SAMPLE["archive_sha256"]:
        raise ValueError("archive is not the preregistered official sample sha256")
    if source.get("bytes") != OFFICIAL_SAMPLE["archive_bytes"]:
        raise ValueError("archive is not the preregistered official sample byte count")
    return {
        "schema_version": PROVENANCE_VERSION,
        "source_kind": "official_sample_archive",
        **OFFICIAL_SAMPLE,
        "entry_manifest_sha256": source["entry_manifest_sha256"],
        "content_sha256": source["content_sha256"],
        "augmentation_seed": AUGMENTATION_SEED,
        "augmentation_config_sha256": FROZEN_PROFILE_SHA256,
    }


def _iter_resources(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for child in value:
            yield from _iter_resources(child)
        return
    if not isinstance(value, dict):
        return
    if value.get("resourceType") == "Bundle":
        for entry in value.get("entry", []):
            if isinstance(entry, dict):
                yield from _iter_resources(entry.get("resource"))
        return
    if isinstance(value.get("resourceType"), str):
        yield value


def _load_patients(payloads: list[tuple[str, bytes]]) -> list[dict[str, Any]]:
    patients: dict[str, dict[str, Any]] = {}
    for logical_path, raw in payloads:
        value = _loads(raw, logical_path)
        for resource in _iter_resources(value):
            if resource.get("resourceType") != "Patient":
                continue
            patient_id = resource.get("id")
            if not isinstance(patient_id, str) or not patient_id:
                raise ValueError(f"source Patient has no id: {logical_path}")
            if patient_id in patients:
                raise ValueError(f"duplicate source Patient id: {patient_id}")
            patients[patient_id] = copy.deepcopy(resource)
    if len(patients) != REQUIRED_SOURCE_PATIENTS:
        raise ValueError(
            f"source has {len(patients)} unique Patients; need exactly "
            f"{REQUIRED_SOURCE_PATIENTS}"
        )
    return sorted(
        patients.values(),
        key=lambda patient: (
            hashlib.sha256(
                f"{AUGMENTATION_SEED}\x00{patient['id']}".encode()
            ).digest(),
            patient["id"],
        ),
    )


def _partition_patients(
    patients: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if len(patients) != REQUIRED_SOURCE_PATIENTS:
        raise ValueError("patient partition requires the frozen 115-patient source")
    return patients[:15], patients[15:]


def _reference(target: dict[str, Any], requested_version: str | None = None) -> str:
    version = requested_version or str(target["meta"]["versionId"])
    return f"{resource_ref(target)}/_history/{version}"


def _resource(
    resource_type: str,
    opaque_id: str,
    patient_ref: str,
    **fields: Any,
) -> dict[str, Any]:
    value = {
        "resourceType": resource_type,
        "id": opaque_id,
        "meta": {"versionId": "1"},
        "subject": {"reference": patient_ref},
    }
    value.update(fields)
    return value


def _terminal_fact(terminal: dict[str, Any]) -> dict[str, str]:
    if terminal["resourceType"] == "Observation":
        coding = terminal["valueCodeableConcept"]["coding"][0]
        return {"code": coding["code"], "display": coding["display"]}
    coding = terminal["type"]["coding"][0]
    return {"code": coding["code"], "display": coding["display"]}


def _question_text(
    question_id: str,
    family: Family,
    depth: int,
    temporal_policy: str,
) -> str:
    target = "organism was found" if family.terminal_type == "Observation" else "specimen was used"
    root = "DiagnosticReport" if family.root_type == "DiagnosticReport" else "Observation"
    intermediate = " through an intermediate observation" if depth == 3 else ""
    return (
        f"For synthetic record {question_id}, what {target} in the "
        f"{temporal_policy} culture {root}{intermediate}?"
    )


def _append_relation(
    source: dict[str, Any], relation: str, target: dict[str, Any], *, requested_version: str | None = None
) -> None:
    reference = {"reference": _reference(target, requested_version)}
    if relation in {OBS_MEMBER, DR_RESULT}:
        field = relation.split(".", 1)[1]
        source.setdefault(field, []).append(reference)
    elif relation == OBS_SPECIMEN:
        source["specimen"] = reference
    else:
        raise ValueError(f"unsupported relation: {relation}")


def _make_case(
    patient: dict[str, Any],
    split: str,
    ordinal: int,
    family: Family,
    depth: int,
    temporal_policy: str,
    failure_mode: str | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    patient_id = patient["id"]
    patient_ref = f"Patient/{patient_id}"
    question_id = f"a11q-{_opaque(patient_id, split, str(ordinal), length=20)}"
    question = _question_text(question_id, family, depth, temporal_policy)
    signature = family.signature(depth)
    question_plan = plan_question(
        question, version=A11_DEPTH_AWARE_QUESTION_PLANNER_VERSION
    )
    if question_plan["path_signatures"] != [signature]:
        raise ValueError(f"question plan does not match generated cell: {question_id}")

    token = _opaque(patient_id, split, str(ordinal), "fact", length=10).upper()
    terminal_id = _opaque(patient_id, split, str(ordinal), "z")
    if family.terminal_type == "Observation":
        terminal = _resource(
            "Observation",
            terminal_id,
            patient_ref,
            status="final",
            code={"text": "Synthetic microbiology finding"},
            valueCodeableConcept={
                "coding": [
                    {
                        "system": "https://example.invalid/a11-organism",
                        "code": f"O-{token}",
                        "display": f"Synthetic organism {token}",
                    }
                ]
            },
        )
    else:
        terminal = _resource(
            "Specimen",
            terminal_id,
            patient_ref,
            status="available",
            type={
                "coding": [
                    {
                        "system": "https://example.invalid/a11-specimen",
                        "code": f"S-{token}",
                        "display": f"Synthetic sample {token}",
                    }
                ],
                "text": f"Synthetic sample {token}",
            },
        )
    fact = _terminal_fact(terminal)

    early_id = _opaque(patient_id, split, str(ordinal), "a")
    late_id = _opaque(patient_id, split, str(ordinal), "b")
    root_ids = [early_id, late_id]
    root_times = ["2099-01-15T08:00:00-05:00", "2100-01-15T13:00:00Z"]
    roots = [
        _resource(
            family.root_type,
            root_id,
            patient_ref,
            status="final",
            effectiveDateTime=event_time,
            code={"text": "Synthetic microbiology culture"},
        )
        for root_id, event_time in zip(root_ids, root_times, strict=True)
    ]
    selected_index = 0 if temporal_policy == "first" else 1
    selected_root = roots[selected_index]
    distractor_root = roots[1 - selected_index]

    resources: list[dict[str, Any]] = roots[:]
    entries: list[dict[str, Any]] = [
        {"practice_id": PRACTICE_ID, "resource": resource} for resource in roots
    ]
    missing_distractor = _resource(
        "Observation", _opaque(patient_id, split, str(ordinal), "d"), patient_ref
    )
    _append_relation(distractor_root, family.first_relation, missing_distractor)

    current = selected_root
    bridges: list[dict[str, Any]] = []
    bridge_count = depth - 1
    for bridge_index in range(bridge_count):
        bridge = _resource(
            "Observation",
            _opaque(patient_id, split, str(ordinal), f"m{bridge_index}"),
            patient_ref,
            status="final",
            code={"text": "Synthetic microbiology panel"},
        )
        relation = family.first_relation if bridge_index == 0 else OBS_MEMBER
        _append_relation(current, relation, bridge)
        bridges.append(bridge)
        resources.append(bridge)
        entries.append({"practice_id": PRACTICE_ID, "resource": bridge})
        current = bridge

    requested_version = "1"
    if failure_mode == "stale_version":
        terminal["meta"]["versionId"] = "2"
    _append_relation(
        current,
        family.terminal_relation,
        terminal,
        requested_version=requested_version,
    )
    if failure_mode != "missing":
        resources.append(terminal)
        entries.append(
            {
                "practice_id": (
                    DENIED_PRACTICE_ID if failure_mode == "out_of_scope" else PRACTICE_ID
                ),
                "resource": terminal,
            }
        )

    max_targets = depth - 1 if failure_mode == "bound_exhaustion" else 8
    source_case = {
        "case_id": question_id,
        "patient_ref": patient_ref,
        "principal": {
            "principal_id": PRINCIPAL_ID,
            "practice_id": PRACTICE_ID,
            "purpose": PURPOSE,
        },
        "allowed_purposes": [PURPOSE],
        "seed_refs": sorted(resource_ref(root) for root in roots),
        "vocabulary_allowed_resource_types": ALLOWED_TYPES,
        "max_depth": depth,
        "max_targets": max_targets,
        "max_packet_bytes": FROZEN_PROFILE["max_packet_bytes"],
        "resources": entries,
    }
    question_row = {
        "question_id": question_id,
        "split": split,
        "question": question,
        "assumption": "All records and identifiers are synthetic and non-PHI.",
        "patient_fhir_id": patient_id,
        "evidence_recipe": a6.A11_DEPTH_AWARE_EVIDENCE_RECIPE,
        "question_plan": question_plan,
        "family": family.family_id,
        "depth": depth,
        "temporal_policy": temporal_policy,
    }
    gold_row = {
        "question_id": question_id,
        "answerable": failure_mode is None,
        "failure_mode": failure_mode,
        "reference_answer": fact if failure_mode is None else None,
        "selected_root_ref": resource_ref(selected_root),
        "terminal_resource_ref": resource_ref(terminal),
        "path_signature": signature,
        "depth": depth,
    }
    return source_case, question_row, gold_row


def _rows_for_split(
    patients: list[dict[str, Any]], split: str, question_count: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not patients:
        raise ValueError(f"{split} patient partition is empty")
    per_cell = question_count // (len(FAMILIES) * 2)
    if per_cell * len(FAMILIES) * 2 != question_count:
        raise ValueError("question count is not balanced across family-depth cells")
    cells = [(family, depth) for family in FAMILIES for depth in (2, 3)]
    specs: list[tuple[Family, int, int]] = []
    for occurrence in range(per_cell):
        for family, depth in cells:
            specs.append((family, depth, occurrence))

    source_rows = []
    question_rows = []
    gold_rows = []
    for ordinal, (family, depth, occurrence) in enumerate(specs):
        patient = patients[ordinal % len(patients)]
        cell_index = cells.index((family, depth))
        temporal = "first" if (occurrence + cell_index) % 2 == 0 else "latest"
        if split == "efficacy" and occurrence < 3:
            failure_mode = FAILURE_MODES[(cell_index * 3 + occurrence) % 4]
        elif split == "development" and occurrence == 0:
            failure_mode = FAILURE_MODES[cell_index % 4]
        else:
            failure_mode = None
        source, question, gold = _make_case(
            patient,
            split,
            ordinal,
            family,
            depth,
            temporal,
            failure_mode,
        )
        source_rows.append(source)
        question_rows.append(question)
        gold_rows.append(gold)
    return source_rows, question_rows, gold_rows


def _relation_for_step(step: dict[str, Any]) -> str:
    segments = [segment for segment in step["json_pointer"].split("/") if segment]
    if len(segments) not in {1, 2}:
        raise ValueError("registered path has a noncanonical JSON pointer")
    field = segments[0].replace("~1", "/").replace("~0", "~")
    relation = f"{step['source'].split('/', 1)[0]}.{field}"
    target_type = step["target_type"]
    shape = "singular" if len(segments) == 1 else "repeating"
    if (step["source"].split("/", 1)[0], field, target_type, shape) not in REGISTERED_REFERENCE_PATHS:
        raise ValueError("projection traversed an unregistered relation")
    return relation


def _reject_source_labels(value: Any, location: str = "source") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in FORBIDDEN_SOURCE_KEYS or lowered.startswith(FORBIDDEN_SOURCE_PREFIXES):
                raise ValueError(f"forbidden model-source key at {location}.{key}")
            _reject_source_labels(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_source_labels(child, f"{location}[{index}]")


def _audit_case(
    source: dict[str, Any], question: dict[str, Any], gold: dict[str, Any]
) -> dict[str, Any]:
    _reject_source_labels(source)
    ids: set[tuple[str, str]] = set()
    for entry in source["resources"]:
        resource = entry["resource"]
        key = (resource["resourceType"], resource["id"])
        if key in ids:
            raise ValueError(f"duplicate resource id in {source['case_id']}: {key}")
        ids.add(key)
        if not str(resource.get("meta", {}).get("versionId", "")):
            raise ValueError(f"augmented resource lacks version: {resource_ref(resource)}")
        if any(fragment in resource["id"].lower() for fragment in FORBIDDEN_ID_FRAGMENTS):
            raise ValueError("generated resource id encodes a benchmark stratum")
        subject = resource.get("subject")
        if (
            not isinstance(subject, dict)
            or subject.get("reference") != source["patient_ref"]
        ):
            raise ValueError("augmented resource is not explicitly patient-bound")

    plan = plan_question(
        question["question"], version=A11_DEPTH_AWARE_QUESTION_PLANNER_VERSION
    )
    if plan != question["question_plan"]:
        raise ValueError("sealed question plan is stale")
    if plan.get("path_depth") != question["depth"]:
        raise ValueError("question depth wording is ambiguous")

    star = project_star(source)
    traversal = project_traversal(source)
    star_serialized = canonical_bytes(star).decode("utf-8")
    star_lower = star_serialized.lower()
    traversal_refs = {resource_ref(resource) for resource in traversal["resources"]}
    terminal_ref = gold["terminal_resource_ref"]
    terminal_id = terminal_ref.split("/", 1)[1]
    if terminal_ref in {resource_ref(resource) for resource in star["resources"]}:
        raise ValueError("V is star-answerable")
    if terminal_id.lower() in star_lower:
        raise ValueError("V leaks the terminal identifier or alias")
    reference_answer = gold["reference_answer"]
    if reference_answer is not None:
        for alias in reference_answer.values():
            if alias and alias.lower() in star_lower:
                raise ValueError("V leaks a terminal answer alias")

    source_index = {
        resource_ref(entry["resource"]): entry["resource"]
        for entry in source["resources"]
        if entry["practice_id"] == PRACTICE_ID
    }
    authorized_refs = set(source_index)
    for packet_name, packet in (("V", star), ("T", traversal)):
        for resource in packet["resources"]:
            reference = resource_ref(resource)
            if reference not in authorized_refs:
                raise ValueError(f"{packet_name} leaks a cross-practice resource")
            subject = resource.get("subject")
            subject_reference = (
                subject.get("reference") if isinstance(subject, dict) else None
            )
            if (
                isinstance(subject_reference, str)
                and subject_reference != source["patient_ref"]
            ):
                raise ValueError(f"{packet_name} leaks a cross-patient resource")
    routes = []
    for citation in traversal["audit_path_citations"]:
        steps = citation["steps"]
        for step in steps:
            source_resource = source_index.get(step["source"])
            if source_resource is None or (
                step["json_pointer"], step["requested_reference"]
            ) not in set(iter_explicit_references(source_resource)):
                raise ValueError("traversal path cannot replay against source JSON")
        if (
            steps
            and steps[0]["source"] == gold["selected_root_ref"]
            and citation["resolved_target"] == terminal_ref
            and citation["state"] == "available"
        ):
            routes.append([_relation_for_step(step) for step in steps])

    answerable = gold["answerable"]
    event_packet = compile_event_groups(traversal, plan)
    event_state = event_packet["answerability_receipt"]["state"]
    if answerable:
        if terminal_ref not in traversal_refs:
            raise ValueError("answerable traversal does not contain terminal")
        if routes != [gold["path_signature"]]:
            raise ValueError("terminal does not have exactly one registered route")
        shortest = min(len(route) for route in routes)
        if shortest != gold["depth"]:
            raise ValueError("terminal is reachable by an alternate shorter path")
        if event_state != "sufficient":
            raise ValueError("answerable E packet is not sufficient")
    else:
        if terminal_ref in traversal_refs:
            raise ValueError("unanswerable traversal exposes terminal")
        if routes:
            raise ValueError("unanswerable terminal has an available route")
        if event_state != "insufficient":
            raise ValueError("unanswerable E packet is not insufficient")

    timed_roots, missing_time = rank_event_roots(
        source_index,
        source["seed_refs"],
        version=A11_NORMALIZED_EVENT_RANK_VERSION,
    )
    if missing_time:
        raise ValueError("generated root lacks canonical clinical time")
    selected = timed_roots[0 if question["temporal_policy"] == "first" else -1][3]
    if selected != gold["selected_root_ref"]:
        raise ValueError("normalized UTC event rank selected the wrong root")

    return {
        "question_id": question["question_id"],
        "star_terminal_absent": True,
        "star_alias_absent": True,
        "paths_replay": True,
        "exact_shortest_path": True,
        "scope_leakage": 0,
        "answerability_matches": True,
        "normalized_utc_rank_matches": True,
    }


def _audit_dataset(
    sources: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    gold: list[dict[str, Any]],
    development_patients: list[dict[str, Any]],
    efficacy_patients: list[dict[str, Any]],
) -> dict[str, Any]:
    if not (len(sources) == len(questions) == len(gold) == 144):
        raise ValueError("dataset must contain exactly 144 questions")
    question_ids = [row["question_id"] for row in questions]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("duplicate question id")
    question_texts = [row["question"] for row in questions]
    if len(question_texts) != len(set(question_texts)):
        raise ValueError("duplicate question text")
    if [row["case_id"] for row in sources] != question_ids or [row["question_id"] for row in gold] != question_ids:
        raise ValueError("source, question, and gold order differ")

    development_ids = {patient["id"] for patient in development_patients}
    efficacy_ids = {patient["id"] for patient in efficacy_patients}
    if development_ids & efficacy_ids:
        raise ValueError("patient split overlap")
    if len(development_ids) != 15 or len(efficacy_ids) != 100:
        raise ValueError("patient partition size changed")

    seen_resource_ids: set[tuple[str, str]] = set()
    for source in sources:
        for entry in source["resources"]:
            resource = entry["resource"]
            identity = (resource["resourceType"], resource["id"])
            if identity in seen_resource_ids:
                raise ValueError("augmented resource is reused across question rows")
            seen_resource_ids.add(identity)
    for row in questions:
        expected = development_ids if row["split"] == "development" else efficacy_ids
        if row["patient_fhir_id"] not in expected:
            raise ValueError("question patient leaks across partitions")

    cells = collections.Counter(
        (row["split"], row["family"], row["depth"]) for row in questions
    )
    for family in FAMILY_BY_ID:
        for depth in (2, 3):
            if cells[("development", family, depth)] != 3:
                raise ValueError("development family-depth imbalance")
            if cells[("efficacy", family, depth)] != 15:
                raise ValueError("efficacy family-depth imbalance")

    efficacy_temporal = collections.Counter(
        row["temporal_policy"] for row in questions if row["split"] == "efficacy"
    )
    if efficacy_temporal != {"first": 60, "latest": 60}:
        raise ValueError("efficacy temporal quota changed")
    gold_by_id = {row["question_id"]: row for row in gold}
    efficacy_unanswerable = [
        row for row in questions
        if row["split"] == "efficacy" and not gold_by_id[row["question_id"]]["answerable"]
    ]
    if len(efficacy_unanswerable) != 24:
        raise ValueError("efficacy unanswerable quota changed")
    per_cell_unanswerable = collections.Counter(
        (row["family"], row["depth"]) for row in efficacy_unanswerable
    )
    if set(per_cell_unanswerable.values()) != {3} or len(per_cell_unanswerable) != 8:
        raise ValueError("efficacy unanswerable family-depth quota changed")
    failures = collections.Counter(
        gold_by_id[row["question_id"]]["failure_mode"] for row in efficacy_unanswerable
    )
    if failures != {mode: 6 for mode in FAILURE_MODES}:
        raise ValueError("efficacy failure-mode quota changed")

    efficacy_patient_counts = collections.Counter(
        row["patient_fhir_id"] for row in questions if row["split"] == "efficacy"
    )
    if len(efficacy_patients) == 100 and collections.Counter(efficacy_patient_counts.values()) != {1: 80, 2: 20}:
        raise ValueError("efficacy patient reuse quota changed")

    case_audits = [
        _audit_case(source, question, answer)
        for source, question, answer in zip(sources, questions, gold, strict=True)
    ]
    return {
        "schema_version": "a11-zero-model-audit-v1",
        "model_calls": 0,
        "questions": len(questions),
        "development_questions": DEVELOPMENT_QUESTIONS,
        "efficacy_questions": EFFICACY_QUESTIONS,
        "development_patients": len(development_patients),
        "efficacy_patients": len(efficacy_patients),
        "patient_split_overlap": 0,
        "family_depth_cells": {
            f"{split}:{family}:depth-{depth}": count
            for (split, family, depth), count in sorted(cells.items())
        },
        "efficacy_temporal": dict(sorted(efficacy_temporal.items())),
        "efficacy_unanswerable": len(efficacy_unanswerable),
        "efficacy_failure_modes": dict(sorted(failures.items())),
        "case_audits": case_audits,
        "all_checks_passed": True,
    }


def _construct(
    patients: list[dict[str, Any]], source_receipt: dict[str, Any], provenance: dict[str, Any]
) -> dict[str, bytes]:
    development_patients, efficacy_patients = _partition_patients(patients)
    development = _rows_for_split(
        development_patients, "development", DEVELOPMENT_QUESTIONS
    )
    efficacy = _rows_for_split(efficacy_patients, "efficacy", EFFICACY_QUESTIONS)
    sources = development[0] + efficacy[0]
    questions = development[1] + efficacy[1]
    gold = development[2] + efficacy[2]
    audit = _audit_dataset(
        sources, questions, gold, development_patients, efficacy_patients
    )
    policy_contexts = [_policy_context(source) for source in sources]
    for question, policy in zip(questions, policy_contexts, strict=True):
        question["policy_context_sha256"] = sha256(canonical_bytes(policy))
    order = {
        "schema_version": "a11-question-order-v1",
        "evidence_recipe": a6.A11_DEPTH_AWARE_EVIDENCE_RECIPE,
        "question_planner_version": A11_DEPTH_AWARE_QUESTION_PLANNER_VERSION,
        "question_ids": [row["question_id"] for row in questions],
    }
    snapshot = {
        "schema_version": "a11-source-snapshot-v1",
        "source_epoch": SOURCE_EPOCH,
        "source_receipt": source_receipt,
        "provenance": provenance,
        "unique_source_patients": len(patients),
        "selected_development_patient_sha256": [
            sha256(patient["id"].encode()) for patient in development_patients
        ],
        "selected_efficacy_patient_sha256": [
            sha256(patient["id"].encode()) for patient in efficacy_patients
        ],
    }
    return {
        "source_snapshot.json": _pretty(snapshot),
        "source_corpus.jsonl": _jsonl(sources),
        "questions.jsonl": _jsonl(questions),
        "questions.csv": _question_csv(questions),
        "policy_contexts.jsonl": _jsonl(policy_contexts),
        "gold.jsonl": _jsonl(gold),
        "question_order.json": _pretty(order),
        "zero_model_audit.json": _pretty(audit),
    }


def build_dataset(input_path: Path, provenance_path: Path, output_dir: Path) -> dict[str, Any]:
    source_receipt, payloads = inspect_source(input_path)
    provenance_bytes = provenance_path.read_bytes()
    provenance = _loads(provenance_bytes, provenance_path.name)
    if not isinstance(provenance, dict):
        raise ValueError("source provenance must be an object")
    _validate_provenance(provenance, source_receipt)
    patients = _load_patients(payloads)
    if provenance["source_kind"] == "release_generation" and provenance["population"] != len(patients):
        raise ValueError("source population does not match unique Patient count")

    first = _construct(patients, source_receipt, provenance)
    second = _construct(patients, source_receipt, provenance)
    if first != second:
        raise ValueError("nondeterministic dataset rebuild")

    repo = Path(__file__).resolve().parent
    dependencies = {
        filename: {"sha256": sha256((repo / filename).read_bytes()), "bytes": (repo / filename).stat().st_size}
        for filename in DEPENDENCY_FILES
    }
    manifest = {
        "schema_version": DATASET_VERSION,
        "source_epoch": SOURCE_EPOCH,
        "model_calls": 0,
        "source_input": source_receipt,
        "provenance_input": {
            "logical_path": provenance_path.name,
            "sha256": sha256(provenance_bytes),
            "bytes": len(provenance_bytes),
        },
        "profile": FROZEN_PROFILE,
        "profile_sha256": FROZEN_PROFILE_SHA256,
        "compiler_dependencies": dependencies,
        "artifacts": {
            name: {"sha256": sha256(data), "bytes": len(data)}
            for name, data in sorted(first.items())
        },
        "deterministic_rebuild": True,
    }
    manifest_bytes = _pretty(manifest)
    manifest_sha = sha256(manifest_bytes)
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_names = set(first) | {"manifest.json", "manifest.sha256"}
    extras = {path.name for path in output_dir.iterdir()} - expected_names
    if extras:
        raise ValueError(f"output directory contains unexpected files: {sorted(extras)}")
    for name, data in first.items():
        (output_dir / name).write_bytes(data)
    (output_dir / "manifest.json").write_bytes(manifest_bytes)
    (output_dir / "manifest.sha256").write_text(manifest_sha + "\n", encoding="ascii")
    verify_dataset(output_dir, expected_manifest_sha256=manifest_sha)
    return manifest


def verify_dataset(output_dir: Path, *, expected_manifest_sha256: str) -> dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha = sha256(manifest_bytes)
    sidecar = (output_dir / "manifest.sha256").read_text(encoding="ascii")
    if sidecar != manifest_sha + "\n":
        raise ValueError("manifest sidecar does not match manifest bytes")
    if not _is_hash(expected_manifest_sha256, 64):
        raise ValueError("expected manifest sha256 must be a lowercase sha256")
    if manifest_sha != expected_manifest_sha256:
        raise ValueError("manifest does not match the pinned sha256")
    manifest = _loads(manifest_bytes, "manifest.json")
    if manifest.get("schema_version") != DATASET_VERSION:
        raise ValueError("unsupported dataset manifest")
    if manifest.get("profile") != FROZEN_PROFILE or manifest.get("profile_sha256") != FROZEN_PROFILE_SHA256:
        raise ValueError("dataset profile changed")
    if manifest.get("model_calls") != 0 or manifest.get("deterministic_rebuild") is not True:
        raise ValueError("dataset is not sealed as a deterministic zero-model build")
    expected_names = set(manifest.get("artifacts", {})) | {"manifest.json", "manifest.sha256"}
    actual_names = {path.name for path in output_dir.iterdir()}
    if actual_names != expected_names:
        raise ValueError("dataset artifact set changed")
    if not all((output_dir / name).is_file() for name in actual_names):
        raise ValueError("dataset artifact set contains a non-file")
    for name, receipt in manifest["artifacts"].items():
        data = (output_dir / name).read_bytes()
        if receipt != {"sha256": sha256(data), "bytes": len(data)}:
            raise ValueError(f"dataset artifact changed: {name}")
    dependencies = manifest.get("compiler_dependencies")
    if not isinstance(dependencies, dict) or set(dependencies) != set(DEPENDENCY_FILES):
        raise ValueError("dataset compiler dependency set changed")
    repo = Path(__file__).resolve().parent
    for filename, receipt in dependencies.items():
        data = (repo / filename).read_bytes()
        if receipt != {"sha256": sha256(data), "bytes": len(data)}:
            raise ValueError(f"dataset compiler dependency changed: {filename}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect-source")
    inspect_parser.add_argument("--input", type=Path, required=True)
    provenance_parser = subparsers.add_parser("write-official-sample-provenance")
    provenance_parser.add_argument("--input", type=Path, required=True)
    provenance_parser.add_argument("--output", type=Path, required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--input", type=Path, required=True)
    build_parser.add_argument("--provenance", type=Path, required=True)
    build_parser.add_argument("--output-dir", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--output-dir", type=Path, required=True)
    verify_parser.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    if args.command == "inspect-source":
        receipt, _ = inspect_source(args.input)
        print(_pretty(receipt).decode(), end="")
    elif args.command == "write-official-sample-provenance":
        receipt, _ = inspect_source(args.input)
        provenance = official_sample_provenance(receipt)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(_pretty(provenance))
        print(sha256(_pretty(provenance)))
    elif args.command == "build":
        manifest = build_dataset(args.input, args.provenance, args.output_dir)
        print(sha256(_pretty(manifest)))
    else:
        manifest = verify_dataset(
            args.output_dir,
            expected_manifest_sha256=args.expected_manifest_sha256,
        )
        print(sha256(_pretty(manifest)))


if __name__ == "__main__":
    main()
