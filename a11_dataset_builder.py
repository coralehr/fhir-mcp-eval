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
import os
import re
import stat
import tempfile
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import a6_packet_builder as a6
from a11_governed_retrieval import (
    SOURCE_SNAPSHOT_VERSION,
    build_governed_retrieval_bundle,
)
from a11_evidence_core import (
    REGISTERED_REFERENCE_PATHS,
    canonical_bytes,
    iter_explicit_references,
    parse_relative_reference,
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
from a11_packet_adapter import load_promoted_bundle


DATASET_VERSION = "a11-dataset-v1"
PROVENANCE_VERSION = "a11-source-provenance-v1"
AUGMENTATION_SEED = "a11-four-family-depth-aware-2026-07-15"
SOURCE_EPOCH = "2026-07-15-pre-answer"
REQUIRED_SOURCE_PATIENTS = 115
DEVELOPMENT_QUESTIONS = 24
EFFICACY_QUESTIONS = 120
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ENTRY_BYTES = 96 * 1024 * 1024
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
    "a11_governed_retrieval.py",
    "a11_packet_adapter.py",
    "a6_packet_builder.py",
    "codex_harness.py",
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
    source_case_sha256 = sha256(canonical_bytes(source))
    return {
        "principal_id": source["principal"]["principal_id"],
        "practice_id": source["principal"]["practice_id"],
        "purpose": source["principal"]["purpose"],
        "allowed_purposes": list(source["allowed_purposes"]),
        "patient_ref": source["patient_ref"],
        "source_id": f"a11-case-{_opaque(source['case_id'], 'source', length=20)}",
        "source_version": f"{SOURCE_EPOCH}-{source_case_sha256[:16]}",
        "traversal_bounds": {
            "max_depth": source["max_depth"],
            "max_targets": source["max_targets"],
            "max_packet_bytes": source["max_packet_bytes"],
            "vocabulary_allowed_resource_types": ["Observation", "Specimen"],
        },
    }


def _source_snapshot(
    source: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": SOURCE_SNAPSHOT_VERSION,
        "source_id": policy["source_id"],
        "source_version": policy["source_version"],
        "practice_id": policy["practice_id"],
        "patient_ref": policy["patient_ref"],
        "resources": copy.deepcopy(source["resources"]),
    }


class _DatasetFhirClient:
    """Minimal deterministic FHIR search facade over the augmented corpus."""

    def __init__(self, sources: list[dict[str, Any]]) -> None:
        resources: dict[str, dict[str, Any]] = {}
        for source in sources:
            for entry in source["resources"]:
                if entry["practice_id"] != PRACTICE_ID:
                    continue
                resource = entry["resource"]
                reference = resource_ref(resource)
                if reference in resources:
                    raise ValueError("preflight FHIR store has a duplicate resource")
                resources[reference] = resource
        self._resources = resources

    def search_with_pagination(
        self, query_string: str, *, max_results: int | None = None
    ) -> list[dict[str, Any]]:
        resource_type, separator, query = query_string.partition("?")
        if not separator:
            raise ValueError("preflight FHIR query has no parameters")
        params = urllib.parse.parse_qs(query, keep_blank_values=True)
        patient_values = params.get("patient")
        if not patient_values or len(patient_values) != 1:
            raise ValueError("preflight FHIR query has no unique patient")
        patient_ref = f"Patient/{patient_values[0]}"
        code_terms = [value.casefold() for value in params.get("code:text", [])]
        resources = []
        for resource in self._resources.values():
            if resource.get("resourceType") != resource_type:
                continue
            subject = resource.get("subject")
            if not isinstance(subject, dict) or subject.get("reference") != patient_ref:
                continue
            code = resource.get("code")
            code_text = code.get("text", "") if isinstance(code, dict) else ""
            if code_terms and not all(term in str(code_text).casefold() for term in code_terms):
                continue
            resources.append(resource)

        by_ref = {resource_ref(resource): resource for resource in resources}
        ranked, missing_time = rank_event_roots(
            by_ref,
            list(by_ref),
            version=A11_NORMALIZED_EVENT_RANK_VERSION,
        )
        if missing_time:
            raise ValueError("preflight FHIR root has no canonical event time")
        ordered = [by_ref[row[3]] for row in ranked]
        sort_values = params.get("_sort", [])
        if sort_values and sort_values[0].startswith("-"):
            ordered.reverse()
        return copy.deepcopy(
            ordered if max_results is None else ordered[:max_results]
        )


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


def _update_content_hash(
    digest: Any, logical_path: str, data: bytes
) -> None:
    encoded = logical_path.encode("utf-8")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    digest.update(len(data).to_bytes(8, "big"))
    digest.update(data)


def _collect_patients(
    patients: dict[str, dict[str, Any]], raw: bytes, logical_path: str
) -> None:
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


def _read_regular_file_at(root_fd: int, relative: PurePosixPath) -> tuple[bytes, int]:
    """Read one directory-source file without following any path-component link."""

    if not relative.parts:
        raise ValueError("source directory entry has no relative path")
    directory_fd = os.dup(root_fd)
    try:
        for part in relative.parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            relative.parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        try:
            file_stat = os.fstat(file_fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError(f"source directory entry is not regular: {relative}")
            if file_stat.st_size > MAX_ENTRY_BYTES:
                raise ValueError(f"source entry exceeds byte bound: {relative}")
            chunks = []
            received = 0
            while True:
                chunk = os.read(file_fd, min(1024 * 1024, MAX_ENTRY_BYTES + 1 - received))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received > MAX_ENTRY_BYTES:
                    raise ValueError(f"source entry exceeds byte bound: {relative}")
            if received != file_stat.st_size:
                raise ValueError(f"source entry byte count changed: {relative}")
            return b"".join(chunks), received
        finally:
            os.close(file_fd)
    except OSError as exc:
        raise ValueError(f"unsafe source directory entry: {relative}") from exc
    finally:
        os.close(directory_fd)


def inspect_source(input_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a path-stable receipt and Patients from one verified read pass."""

    input_path = input_path.resolve()
    patients: dict[str, dict[str, Any]] = {}
    content_digest = hashlib.sha256()
    if input_path.is_file():
        archive_bytes = input_path.stat().st_size
        if archive_bytes > MAX_ARCHIVE_BYTES:
            raise ValueError("source archive exceeds byte bound")
        seen: set[str] = set()
        receipts: list[dict[str, Any]] = []
        total = 0
        archive_digest = hashlib.sha256()
        git_blob_digest = hashlib.sha1(usedforsecurity=False)
        git_blob_digest.update(f"blob {archive_bytes}\0".encode("ascii"))
        copied = 0
        with tempfile.TemporaryFile() as snapshot:
            with input_path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    copied += len(chunk)
                    if copied > MAX_ARCHIVE_BYTES:
                        raise ValueError("source archive exceeds byte bound")
                    archive_digest.update(chunk)
                    git_blob_digest.update(chunk)
                    snapshot.write(chunk)
            if copied != archive_bytes:
                raise ValueError("source archive byte count changed during snapshot")
            snapshot.seek(0)
            if not zipfile.is_zipfile(snapshot):
                raise ValueError("source file must be a ZIP archive")
            snapshot.seek(0)
            with zipfile.ZipFile(snapshot) as handle:
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
                        _update_content_hash(content_digest, info.filename, data)
                        _collect_patients(patients, data, info.filename)
        if not patients:
            raise ValueError("source archive contains no JSON entries")
        receipt = {
            "kind": "zip",
            "logical_path": "source.zip",
            "sha256": archive_digest.hexdigest(),
            "git_blob_sha1": git_blob_digest.hexdigest(),
            "bytes": archive_bytes,
            "entries": receipts,
            "entry_manifest_sha256": sha256(canonical_bytes(receipts)),
            "content_sha256": content_digest.hexdigest(),
        }
        return receipt, list(patients.values())

    if not input_path.is_dir():
        raise ValueError("source input does not exist")
    receipts = []
    paths = []
    json_sizes: dict[str, int] = {}
    total = 0
    for entry_count, path in enumerate(input_path.rglob("*"), start=1):
        if entry_count > MAX_ARCHIVE_ENTRIES:
            raise ValueError("source directory exceeds entry bound")
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"source directory contains a symbolic link: {path.name}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"source directory contains a non-regular entry: {path.name}")
        total += metadata.st_size
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError("source directory exceeds total byte bound")
        if path.suffix.lower() == ".json":
            paths.append(path)
            json_sizes[path.relative_to(input_path).as_posix()] = metadata.st_size
    paths.sort()
    root_fd = os.open(input_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for path in paths:
            relative_path = PurePosixPath(path.relative_to(input_path).as_posix())
            data, entry_bytes = _read_regular_file_at(root_fd, relative_path)
            total += entry_bytes - json_sizes[relative_path.as_posix()]
            if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise ValueError("source directory exceeds total byte bound")
            relative = relative_path.as_posix()
            receipts.append(
                {"path": relative, "sha256": sha256(data), "bytes": entry_bytes}
            )
            _update_content_hash(content_digest, relative, data)
            _collect_patients(patients, data, relative)
    finally:
        os.close(root_fd)
    if not patients:
        raise ValueError("source directory contains no JSON files")
    receipt = {
        "kind": "directory",
        "logical_path": "source-directory",
        "entries": receipts,
        "entry_manifest_sha256": sha256(canonical_bytes(receipts)),
        "content_sha256": content_digest.hexdigest(),
    }
    receipt["sha256"] = sha256(canonical_bytes(receipts))
    receipt["bytes"] = sum(item["bytes"] for item in receipts)
    return receipt, list(patients.values())


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
        if provenance.get("git_blob_sha1") != source["git_blob_sha1"]:
            raise ValueError("source archive git blob changed")
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


def _load_patients(patient_resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patients: dict[str, dict[str, Any]] = {}
    for resource in patient_resources:
        patient_id = resource["id"]
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


def _make_terminal(
    patient_id: str,
    patient_ref: str,
    split: str,
    ordinal: int,
    family: Family,
    branch: str,
) -> dict[str, Any]:
    token = _opaque(
        patient_id, split, str(ordinal), branch, "fact", length=10
    ).upper()
    terminal_id = _opaque(patient_id, split, str(ordinal), branch, "terminal")
    if family.terminal_type == "Observation":
        return _resource(
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
    return _resource(
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

    terminal = _make_terminal(
        patient_id, patient_ref, split, ordinal, family, "selected"
    )
    distractor_terminal = _make_terminal(
        patient_id, patient_ref, split, ordinal, family, "nonselected"
    )
    fact = _terminal_fact(terminal)
    distractor_fact = _terminal_fact(distractor_terminal)
    if distractor_fact == fact:
        raise ValueError("temporal roots have identical terminal facts")

    selected_index = 0 if temporal_policy == "first" else 1
    root_ids = sorted(
        [
            _opaque(patient_id, split, str(ordinal), "root-a"),
            _opaque(patient_id, split, str(ordinal), "root-b"),
        ]
    )
    if failure_mode == "bound_exhaustion" and selected_index == 0:
        root_ids.reverse()
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
    selected_root = roots[selected_index]
    distractor_root = roots[1 - selected_index]

    entries: list[dict[str, Any]] = [
        {"practice_id": PRACTICE_ID, "resource": resource} for resource in roots
    ]

    def append_branch(
        root: dict[str, Any], branch: str, branch_terminal: dict[str, Any]
    ) -> None:
        current = root
        for bridge_index in range(depth - 1):
            bridge = _resource(
                "Observation",
                _opaque(
                    patient_id,
                    split,
                    str(ordinal),
                    branch,
                    f"bridge-{bridge_index}",
                ),
                patient_ref,
                status="final",
                code={"text": "Synthetic microbiology panel"},
            )
            relation = family.first_relation if bridge_index == 0 else OBS_MEMBER
            _append_relation(current, relation, bridge)
            entries.append({"practice_id": PRACTICE_ID, "resource": bridge})
            current = bridge
        _append_relation(
            current,
            family.terminal_relation,
            branch_terminal,
            requested_version="1",
        )

    append_branch(distractor_root, "nonselected", distractor_terminal)
    entries.append(
        {"practice_id": PRACTICE_ID, "resource": distractor_terminal}
    )
    if failure_mode == "stale_version":
        terminal["meta"]["versionId"] = "2"
    append_branch(selected_root, "selected", terminal)
    if failure_mode != "missing":
        entries.append(
            {
                "practice_id": (
                    DENIED_PRACTICE_ID if failure_mode == "out_of_scope" else PRACTICE_ID
                ),
                "resource": terminal,
            }
        )

    max_targets = 2 * depth - 1 if failure_mode == "bound_exhaustion" else 8
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
        "nonselected_terminal_resource_ref": resource_ref(distractor_terminal),
        "nonselected_reference_answer": distractor_fact,
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

    if split == "development" and len(patients) == 15 and question_count == 24:
        patient_indices = [*range(15), 0, 4, 5, 6, 7, 1, 2, 3, 8]
    elif split == "efficacy" and len(patients) == 100 and question_count == 120:
        patient_indices = [*range(100), *range(20)]
    else:
        raise ValueError("patient schedule does not match the frozen split profile")

    source_rows = []
    question_rows = []
    gold_rows = []
    for ordinal, (family, depth, occurrence) in enumerate(specs):
        patient = patients[patient_indices[ordinal]]
        cell_index = cells.index((family, depth))
        temporal = "first" if (occurrence + cell_index) % 2 == 0 else "latest"
        if split == "efficacy" and occurrence < 3:
            failure_mode = FAILURE_MODES[(cell_index // 2 + occurrence) % 4]
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
    nonselected_routes = []
    nonselected_terminal_ref = gold["nonselected_terminal_resource_ref"]
    selected_unavailable = []
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
        if (
            steps
            and steps[0]["source"] != gold["selected_root_ref"]
            and citation["resolved_target"] == nonselected_terminal_ref
            and citation["state"] == "available"
        ):
            nonselected_routes.append(
                [_relation_for_step(step) for step in steps]
            )
        requested = parse_relative_reference(citation["requested_target"])
        if (
            steps
            and steps[0]["source"] == gold["selected_root_ref"]
            and requested is not None
            and f"{requested[0]}/{requested[1]}" == terminal_ref
            and citation["state"] == "unavailable"
        ):
            selected_unavailable.append(citation)

    answerable = gold["answerable"]
    event_packet = compile_event_groups(traversal, plan)
    event_state = event_packet["answerability_receipt"]["state"]
    if nonselected_terminal_ref not in traversal_refs:
        raise ValueError("nonselected temporal event lacks a complete terminal")
    if nonselected_routes != [gold["path_signature"]]:
        raise ValueError("nonselected temporal event lacks one registered route")
    if gold["nonselected_reference_answer"] == gold["reference_answer"]:
        raise ValueError("temporal events do not carry distinct facts")
    selected_groups = [
        group
        for group in event_packet["event_groups"]
        if group["temporal_rank"]["selected_for_question"]
    ]
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
        if len(selected_groups) != 1:
            raise ValueError("answerable E packet does not select one temporal event")
    else:
        if terminal_ref in traversal_refs:
            raise ValueError("unanswerable traversal exposes terminal")
        if routes:
            raise ValueError("unanswerable terminal has an available route")
        if event_state != "insufficient":
            raise ValueError("unanswerable E packet is not insufficient")
        if len(selected_unavailable) != 1:
            raise ValueError("unanswerable selected event lacks one unavailable route")

        terminal_entries = [
            entry
            for entry in source["resources"]
            if resource_ref(entry["resource"]) == terminal_ref
        ]
        failure_mode = gold["failure_mode"]
        if failure_mode == "missing":
            mechanism_ok = not terminal_entries
        elif failure_mode == "stale_version":
            mechanism_ok = (
                len(terminal_entries) == 1
                and terminal_entries[0]["practice_id"] == PRACTICE_ID
                and terminal_entries[0]["resource"]["meta"]["versionId"] == "2"
                and selected_unavailable[0]["requested_target"].endswith(
                    "/_history/1"
                )
            )
        elif failure_mode == "out_of_scope":
            mechanism_ok = (
                len(terminal_entries) == 1
                and terminal_entries[0]["practice_id"] == DENIED_PRACTICE_ID
            )
        elif failure_mode == "bound_exhaustion":
            mechanism_ok = (
                len(terminal_entries) == 1
                and terminal_entries[0]["practice_id"] == PRACTICE_ID
                and "target_limit" in traversal["bounds"]["outcomes"]
            )
        else:
            mechanism_ok = False
        if not mechanism_ok:
            raise ValueError(f"unanswerable mechanism does not match {failure_mode}")

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
        "temporal_competitor_complete": True,
        "failure_mechanism_matches": True,
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

    questions_by_patient: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in questions:
        questions_by_patient[row["patient_fhir_id"]].append(row)
    for patient_questions in questions_by_patient.values():
        if len(patient_questions) > 2:
            raise ValueError("patient contributes more than two question rows")
        if len(patient_questions) == 2:
            root_types = {
                FAMILY_BY_ID[row["family"]].root_type for row in patient_questions
            }
            if root_types != {"DiagnosticReport", "Observation"}:
                raise ValueError("reused patient has an ambiguous root-type search")

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
    failure_temporal = collections.Counter(
        (
            gold_by_id[row["question_id"]]["failure_mode"],
            row["temporal_policy"],
        )
        for row in efficacy_unanswerable
    )
    if failure_temporal != {
        (mode, temporal): 3
        for mode in FAILURE_MODES
        for temporal in ("first", "latest")
    }:
        raise ValueError("efficacy failure modes are temporally confounded")

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
        "efficacy_failure_mode_temporal": {
            f"{mode}:{temporal}": failure_temporal[(mode, temporal)]
            for mode in FAILURE_MODES
            for temporal in ("first", "latest")
        },
        "case_audits": case_audits,
        "all_checks_passed": True,
    }


def _jsonl_values(data: bytes, *, location: str) -> list[dict[str, Any]]:
    values = []
    for line_number, line in enumerate(data.splitlines(), start=1):
        if not line:
            continue
        value = _loads(line, f"{location}:{line_number}")
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row is not an object: {location}:{line_number}")
        values.append(value)
    return values


def _governed_preflight(artifacts: dict[str, bytes]) -> bytes:
    """Exercise every row through producer, adapter, and governed T/E bytes."""

    sources = _jsonl_values(
        artifacts["source_corpus.jsonl"], location="source_corpus.jsonl"
    )
    questions = _jsonl_values(
        artifacts["questions.jsonl"], location="questions.jsonl"
    )
    policies = _jsonl_values(
        artifacts["policy_contexts.jsonl"], location="policy_contexts.jsonl"
    )
    gold = _jsonl_values(artifacts["gold.jsonl"], location="gold.jsonl")
    if not (len(sources) == len(questions) == len(policies) == len(gold) == 144):
        raise ValueError("governed preflight artifact counts changed")

    client = _DatasetFhirClient(sources)
    features = a6.resolve_evidence_recipe(
        a6.A11_DEPTH_AWARE_EVIDENCE_RECIPE,
        explicit_features=frozenset(),
        planner="question-only",
    )
    records = []
    for question in questions:
        safe = {field: question.get(field) for field in a6.QUESTION_ONLY_FIELDS}
        intent = a6.qo_infer_intent(
            safe,
            planner_version=a6.A11_QO_PLANNER_VERSION,
        )
        plan = a6.build_search_plan(safe, intent, count=100, features=features)
        resources_by_query = a6.fetch_resources(
            plan,
            per_query_cap=4 * a6.A6A_MAX_TOTAL_RESOURCES,
            client=client,
        )
        records.append(
            a6.build_packet_record(
                safe,
                plan_only=False,
                resources_by_query=resources_by_query,
                count=100,
                planner="question-only",
                max_total_resources=a6.A6A_MAX_TOTAL_RESOURCES,
                max_packet_chars=a6.A6A_MAX_PACKET_CHARS,
                plan=plan,
                features=features,
                evidence_recipe=a6.A11_DEPTH_AWARE_EVIDENCE_RECIPE,
            )
        )

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        questions_path = root / "questions.csv"
        packets_path = root / "v_packets.jsonl"
        manifest_path = root / "v_manifest.json"
        questions_path.write_bytes(artifacts["questions.csv"])
        a6.write_jsonl(packets_path, records)
        manifest_args = argparse.Namespace(
            limit=None,
            count=100,
            plan_only=False,
            split="all",
            question_spec=None,
            planner="question-only",
            features="",
            evidence_recipe=a6.A11_DEPTH_AWARE_EVIDENCE_RECIPE,
            max_total_resources=a6.A6A_MAX_TOTAL_RESOURCES,
            max_packet_chars=a6.A6A_MAX_PACKET_CHARS,
        )
        a6.write_manifest(
            manifest_path,
            input_path=questions_path,
            output_path=packets_path,
            args=manifest_args,
            records=records,
        )
        manifest_sha256 = sha256(manifest_path.read_bytes())
        promoted = load_promoted_bundle(
            packets_path,
            manifest_path,
            expected_manifest_sha256=manifest_sha256,
            expected_evidence_recipe=a6.A11_DEPTH_AWARE_EVIDENCE_RECIPE,
        )

        rows = []
        for source, question, policy, answer in zip(
            sources, questions, policies, gold, strict=True
        ):
            question_id = question["question_id"]
            verified_v = promoted.load(question_id)
            if verified_v["question_plan"] != question["question_plan"]:
                raise ValueError("producer/adapter question plan differs from dataset")
            if verified_v["root_refs"] != source["seed_refs"]:
                raise ValueError("producer/adapter V roots differ from dataset roots")

            policy_bytes = canonical_bytes(policy)
            if sha256(policy_bytes) != question["policy_context_sha256"]:
                raise ValueError("question policy hash changed before governed preflight")
            snapshot_bytes = canonical_bytes(_source_snapshot(source, policy))
            if sha256(snapshot_bytes) != question["source_snapshot_sha256"]:
                raise ValueError("question source snapshot hash changed before preflight")
            governed = build_governed_retrieval_bundle(
                promoted,
                question_id,
                source_snapshot_bytes=snapshot_bytes,
                expected_snapshot_sha256=question["source_snapshot_sha256"],
                policy_context_bytes=policy_bytes,
                expected_policy_sha256=question["policy_context_sha256"],
                expected_evidence_recipe=a6.A11_DEPTH_AWARE_EVIDENCE_RECIPE,
            )
            t_payload = governed.load_flat_model_payload(
                question_id=question_id,
                question=question["question"],
                question_plan=question["question_plan"],
            )
            e_payload = governed.load_event_group_model_payload(
                question_id=question_id,
                question=question["question"],
                question_plan=question["question_plan"],
            )
            receipt = governed.load_receipt()
            event_packet = _loads(e_payload, f"event packet {question_id}")
            expected_state = "sufficient" if answer["answerable"] else "insufficient"
            if event_packet["answerability_receipt"]["state"] != expected_state:
                raise ValueError("governed E answerability differs from gold audit")
            rows.append(
                {
                    "question_id": question_id,
                    "v_model_payload_sha256": verified_v["integrity"][
                        "model_payload_sha256"
                    ],
                    "shared_retrieval_source_sha256": receipt[
                        "shared_retrieval_source_sha256"
                    ],
                    "t_model_payload_sha256": sha256(t_payload),
                    "e_model_payload_sha256": sha256(e_payload),
                    "governed_receipt_sha256": governed.receipt_sha256,
                    "answerability_state": expected_state,
                }
            )

    return _pretty(
        {
            "schema_version": "a11-governed-preflight-v1",
            "model_calls": 0,
            "questions": len(rows),
            "evidence_recipe": a6.A11_DEPTH_AWARE_EVIDENCE_RECIPE,
            "v_manifest_sha256": manifest_sha256,
            "rows": rows,
            "all_checks_passed": True,
        }
    )


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
    for source, question, policy in zip(
        sources, questions, policy_contexts, strict=True
    ):
        question["policy_context_sha256"] = sha256(canonical_bytes(policy))
        question["source_snapshot_sha256"] = sha256(
            canonical_bytes(_source_snapshot(source, policy))
        )
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
    artifacts = {
        "source_snapshot.json": _pretty(snapshot),
        "source_corpus.jsonl": _jsonl(sources),
        "questions.jsonl": _jsonl(questions),
        "questions.csv": _question_csv(questions),
        "policy_contexts.jsonl": _jsonl(policy_contexts),
        "gold.jsonl": _jsonl(gold),
        "question_order.json": _pretty(order),
        "zero_model_audit.json": _pretty(audit),
    }
    artifacts["governed_preflight.json"] = _governed_preflight(artifacts)
    return artifacts


def _dependency_snapshot(repo: Path) -> dict[str, bytes]:
    return {filename: (repo / filename).read_bytes() for filename in DEPENDENCY_FILES}


def _dependency_receipts(snapshot: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    return {
        filename: {"sha256": sha256(data), "bytes": len(data)}
        for filename, data in snapshot.items()
    }


def build_dataset(input_path: Path, provenance_path: Path, output_dir: Path) -> dict[str, Any]:
    repo = Path(__file__).resolve().parent
    dependency_bytes = _dependency_snapshot(repo)
    source_receipt, patient_resources = inspect_source(input_path)
    provenance_bytes = provenance_path.read_bytes()
    provenance = _loads(provenance_bytes, provenance_path.name)
    if not isinstance(provenance, dict):
        raise ValueError("source provenance must be an object")
    _validate_provenance(provenance, source_receipt)
    patients = _load_patients(patient_resources)
    if provenance["source_kind"] == "release_generation" and provenance["population"] != len(patients):
        raise ValueError("source population does not match unique Patient count")

    first = _construct(patients, source_receipt, provenance)
    second = _construct(patients, source_receipt, provenance)
    if first != second:
        raise ValueError("nondeterministic dataset rebuild")

    if _dependency_snapshot(repo) != dependency_bytes:
        raise ValueError("compiler dependencies changed during dataset build")
    dependencies = _dependency_receipts(dependency_bytes)
    manifest = {
        "schema_version": DATASET_VERSION,
        "source_epoch": SOURCE_EPOCH,
        "model_calls": 0,
        "source_input": source_receipt,
        "provenance_input": {
            "logical_path": "source-provenance.json",
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
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(exist_ok=False)
    for name, data in first.items():
        with (output_dir / name).open("xb") as handle:
            handle.write(data)
    with (output_dir / "manifest.json").open("xb") as handle:
        handle.write(manifest_bytes)
    with (output_dir / "manifest.sha256").open("x", encoding="ascii") as handle:
        handle.write(manifest_sha + "\n")
    verify_dataset(output_dir, expected_manifest_sha256=manifest_sha)
    return manifest


def verify_dataset(output_dir: Path, *, expected_manifest_sha256: str) -> dict[str, Any]:
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise ValueError("dataset output directory is unsafe")
    paths = list(output_dir.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("dataset artifact set contains an unsafe entry")
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
    actual_names = {path.name for path in paths}
    if actual_names != expected_names:
        raise ValueError("dataset artifact set changed")
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
        with args.output.open("xb") as handle:
            handle.write(_pretty(provenance))
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
