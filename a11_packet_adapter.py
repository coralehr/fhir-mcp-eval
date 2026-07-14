#!/usr/bin/env python3
"""Seal promoted product packets for the A11 V/T/E experiment.

This adapter deliberately starts from the complete ``compile_evidence.py``
JSONL plus its manifest.  An isolated packet dictionary cannot prove recipe
identity, file integrity, or question-ID uniqueness.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any

import a6_packet_builder as a6
import codex_harness
from a11_event_group_benchmark import plan_question


ADAPTER_VERSION = "a11-promoted-packet-adapter-v1"
A11_MAX_PROMOTED_QUESTIONS = 1_000
A11_MAX_PROMOTED_FILE_BYTES = 256 * 1024 * 1024
A11_MAX_PROMOTED_RECORD_BYTES = 2 * a6.A6A_MAX_PACKET_CHARS + 1024 * 1024

_RECORD_FIELDS = frozenset(
    {
        "question_id",
        "question",
        "patient_fhir_id",
        "assumption",
        "intent",
        "packet",
    }
)
_FORBIDDEN_INPUT_KEYS = frozenset(
    {
        "answer",
        "answerable",
        "expected_answer",
        "expected_event_root",
        "expected_evidence_refs",
        "failure_mode",
        "forbidden_resource_refs",
        "gold",
        "gold_answer",
        "label",
        "minimum_evidence_hops",
        "proc_query",
        "reference_answer",
        "sql_query",
        "true_answer",
        "true_fhir_ids",
    }
)
_FORBIDDEN_INPUT_PREFIXES = ("expected_", "gold_", "true_")
_REGISTERED_ROOT_TYPES = frozenset({"Observation", "DiagnosticReport"})
_MANIFEST_FIELDS = frozenset(
    {"created_at", "kind", "input", "output", "config", "questions", "packet_hashes"}
)
_MANIFEST_CONFIG_FIELDS = frozenset(
    {
        "limit",
        "count",
        "plan_only",
        "split",
        "question_spec",
        "planner",
        "features",
        "evidence_recipe",
        "planner_version",
        "max_total_resources",
        "max_packet_chars",
        "micro_vocabulary",
        "micro_dispatcher",
        "reference_traversal",
    }
)
_INTENT_FIELDS = frozenset(
    {
        "planner",
        "resource_types",
        "search_terms",
        "date_windows",
        "temporal_policy",
        "current_date",
    }
)
_PACKET_FIELDS = frozenset(
    {
        "kind",
        "planner",
        "features",
        "pinned_reference_targets",
        "aggregate_summary",
        "plan_only",
        "resources",
        "resource_count",
        "source_resource_ids",
        "source_queries",
        "bounds",
        "root_fetch_receipt",
        "sha256",
    }
)
_BOUND_FIELDS = frozenset(
    {
        "input_count",
        "kept_count",
        "dropped_count",
        "char_count",
        "char_budget_hit",
        "max_total_resources",
        "max_packet_chars",
        "temporal_policy",
    }
)
_ROOT_FETCH_FIELDS = frozenset(
    {"pre_bound_count", "retained_count", "dropped_count"}
)
_SOURCE_QUERY_FIELDS = frozenset(
    {
        "resource_type",
        "path",
        "reason",
        "relaxation_policy",
        "relaxation_attempts",
        "fetch_receipt",
    }
)
_FETCH_RECEIPT_FIELDS = frozenset(
    {
        "status",
        "initial_result_count",
        "relaxation_attempts",
        "pre_bound_count",
        "retained_count",
        "dropped_count",
    }
)
_RELAXATION_ATTEMPT_FIELDS = frozenset({"path", "result_count"})
_DEPENDENCY_FILES = (
    "a11_evidence_core.py",
    "a11_event_group_benchmark.py",
    "a11_packet_adapter.py",
    "a6_packet_builder.py",
    "codex_harness.py",
    "compile_evidence.py",
)
_BUNDLE_FACTORY_TOKEN = object()


def _canonical_text(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _canonical_bytes(value: Any) -> bytes:
    return _canonical_text(value).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key}")
        value[key] = child
    return value


def _json_loads(data: str | bytes) -> Any:
    return json.loads(data, object_pairs_hook=_unique_object)


def _reject_forbidden_input(value: Any, path: str = "record") -> None:
    if isinstance(value, dict):
        forbidden = {
            str(key)
            for key in value
            if str(key).lower() in _FORBIDDEN_INPUT_KEYS
            or str(key).lower().startswith(_FORBIDDEN_INPUT_PREFIXES)
        }
        if forbidden:
            names = ",".join(sorted(forbidden))
            raise ValueError(f"forbidden benchmark field at {path}: {names}")
        for key, child in value.items():
            _reject_forbidden_input(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_input(child, f"{path}[{index}]")


def _patient_ref(patient_fhir_id: Any) -> str:
    if not isinstance(patient_fhir_id, str):
        raise ValueError("promoted record patient_fhir_id is not a string")
    value = patient_fhir_id.strip()
    if not value:
        raise ValueError("promoted record has no patient_fhir_id")
    raw_id = value.removeprefix("Patient/")
    if "/" in raw_id or a6.FHIR_ID_PATTERN.fullmatch(raw_id) is None:
        raise ValueError("patient_fhir_id is not a Patient reference")
    return f"Patient/{raw_id}"


def _resource_ref(resource: dict[str, Any]) -> str:
    resource_type = resource.get("resourceType")
    resource_id = resource.get("id")
    if not isinstance(resource_type, str) or not resource_type:
        raise ValueError("packet resource has no resourceType")
    if not isinstance(resource_id, str) or not resource_id:
        raise ValueError("packet resource has no id")
    if (
        re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", resource_type) is None
        or a6.FHIR_ID_PATTERN.fullmatch(resource_id) is None
    ):
        raise ValueError("packet resource has an invalid resourceType or id")
    return f"{resource_type}/{resource_id}"


def _planned_root_types(question_plan: dict[str, Any]) -> frozenset[str]:
    signatures = question_plan.get("path_signatures")
    if not isinstance(signatures, list) or not signatures:
        raise ValueError("A11 question plan has no path signatures")
    root_types: set[str] = set()
    for signature in signatures:
        if (
            not isinstance(signature, list)
            or not signature
            or not isinstance(signature[0], str)
            or "." not in signature[0]
        ):
            raise ValueError("A11 question plan path signature is invalid")
        root_types.add(signature[0].split(".", 1)[0])
    if not root_types.issubset(_REGISTERED_ROOT_TYPES):
        raise ValueError("A11 question plan has an unregistered root type")
    return frozenset(root_types)


def _root_refs(
    packet: dict[str, Any], patient_ref: str, root_types: frozenset[str]
) -> list[str]:
    roots: list[str] = []
    for resource in packet.get("resources", []):
        if not isinstance(resource, dict):
            raise ValueError("packet resources must be objects")
        resource_type = resource.get("resourceType")
        if resource_type not in root_types:
            continue
        subject = resource.get("subject")
        if (
            not isinstance(subject, dict)
            or _normalize_patient_reference(subject.get("reference")) != patient_ref
        ):
            raise ValueError(
                f"registered root is not explicitly patient-consistent: {_resource_ref(resource)}"
            )
        roots.append(_resource_ref(resource))
    if not roots:
        raise ValueError("promoted packet has no planned patient-consistent A11 root")
    return sorted(roots)


def _normalize_patient_reference(reference: Any) -> str | None:
    if not isinstance(reference, str):
        return None
    match = re.search(
        r"(?:^|/)Patient/([A-Za-z0-9\-.]{1,64})"
        r"(?:/_history/[A-Za-z0-9\-.]{1,64})?(?:$|[?#])",
        reference,
    )
    return f"Patient/{match.group(1)}" if match is not None else None


def _validate_patient_reference_tree(
    value: Any, *, patient_ref: str, resource_ref: str
) -> None:
    if isinstance(value, dict):
        if "contained" in value:
            raise ValueError(
                f"packet resource unexpectedly contains nested resources: {resource_ref}"
            )
        reference = value.get("reference")
        if isinstance(reference, str):
            normalized = _normalize_patient_reference(reference)
            if normalized is not None and normalized != patient_ref:
                raise ValueError(
                    f"packet resource contains a cross-patient reference: {resource_ref}"
                )
        for child in value.values():
            _validate_patient_reference_tree(
                child, patient_ref=patient_ref, resource_ref=resource_ref
            )
    elif isinstance(value, list):
        for child in value:
            _validate_patient_reference_tree(
                child, patient_ref=patient_ref, resource_ref=resource_ref
            )


def _validate_patient_scope(
    packet: dict[str, Any],
    patient_ref: str,
    *,
    selected_resource_types: frozenset[str],
) -> None:
    for resource in packet.get("resources", []):
        if not isinstance(resource, dict):
            raise ValueError("packet resources must be objects")
        resource_ref = _resource_ref(resource)
        resource_type = resource.get("resourceType")
        if resource_type not in selected_resource_types:
            raise ValueError(
                f"packet resource type was not selected by the question-only plan: {resource_ref}"
            )
        if resource_type == "Patient":
            if resource_ref != patient_ref:
                raise ValueError(
                    f"packet resource is not patient-consistent: {resource_ref}"
                )
        else:
            direct_refs = []
            for field in ("subject", "patient", "beneficiary"):
                candidate = resource.get(field)
                if isinstance(candidate, dict) and isinstance(
                    candidate.get("reference"), str
                ):
                    direct_refs.append(
                        _normalize_patient_reference(candidate["reference"])
                    )
            if not direct_refs or any(ref != patient_ref for ref in direct_refs):
                raise ValueError(
                    f"packet resource is not explicitly patient-consistent: {resource_ref}"
                )
        _validate_patient_reference_tree(
            resource, patient_ref=patient_ref, resource_ref=resource_ref
        )


def _validate_nonclinical_packet_metadata(packet: dict[str, Any]) -> None:
    if packet.get("pinned_reference_targets") != 0:
        raise ValueError("promoted V packet unexpectedly pins reference targets")
    if packet.get("aggregate_summary") is not None:
        raise ValueError("promoted V packet unexpectedly contains an aggregate summary")

    root_fetch = packet.get("root_fetch_receipt")
    if not isinstance(root_fetch, dict) or set(root_fetch) != _ROOT_FETCH_FIELDS:
        raise ValueError("promoted packet root fetch receipt fields changed")
    if not all(
        isinstance(root_fetch.get(field), int)
        and not isinstance(root_fetch.get(field), bool)
        and root_fetch[field] >= 0
        for field in _ROOT_FETCH_FIELDS
    ):
        raise ValueError("promoted packet root fetch receipt is invalid")
    if root_fetch["dropped_count"] != (
        root_fetch["pre_bound_count"] - root_fetch["retained_count"]
    ):
        raise ValueError("promoted packet root fetch receipt is inconsistent")

    source_queries = packet.get("source_queries")
    if not isinstance(source_queries, list) or not source_queries:
        raise ValueError("promoted packet has no source query receipt")
    for query in source_queries:
        if (
            not isinstance(query, dict)
            or not {"resource_type", "path", "reason"}.issubset(query)
            or not set(query).issubset(_SOURCE_QUERY_FIELDS)
        ):
            raise ValueError("promoted packet source query fields changed")
        if not all(
            isinstance(query.get(field), str) and query[field]
            for field in ("resource_type", "path", "reason")
        ):
            raise ValueError("promoted packet source query is invalid")
        fetch_receipt = query.get("fetch_receipt")
        if (
            not isinstance(fetch_receipt, dict)
            or set(fetch_receipt) != _FETCH_RECEIPT_FIELDS
        ):
            raise ValueError("promoted packet query fetch receipt fields changed")
        relaxation_policy = query.get("relaxation_policy")
        if relaxation_policy is not None and not isinstance(relaxation_policy, str):
            raise ValueError("promoted packet query relaxation policy is invalid")
        relaxation_attempts = query.get("relaxation_attempts")
        if relaxation_attempts is not None and (
            not isinstance(relaxation_attempts, list)
            or not all(isinstance(item, str) for item in relaxation_attempts)
        ):
            raise ValueError("promoted packet query relaxation attempts are invalid")
        count_fields = (
            "initial_result_count",
            "pre_bound_count",
            "retained_count",
            "dropped_count",
        )
        fetched_relaxations = fetch_receipt.get("relaxation_attempts")
        if (
            fetch_receipt.get("status") != "ok"
            or not all(
                isinstance(fetch_receipt.get(field), int)
                and not isinstance(fetch_receipt.get(field), bool)
                and fetch_receipt[field] >= 0
                for field in count_fields
            )
            or not isinstance(fetched_relaxations, list)
            or any(
                not isinstance(attempt, dict)
                or set(attempt) != _RELAXATION_ATTEMPT_FIELDS
                or not isinstance(attempt.get("path"), str)
                or not attempt["path"]
                or not isinstance(attempt.get("result_count"), int)
                or isinstance(attempt.get("result_count"), bool)
                or attempt["result_count"] < 0
                for attempt in fetched_relaxations
            )
        ):
            raise ValueError("promoted packet query fetch receipt is invalid")
        if fetch_receipt["dropped_count"] != (
            fetch_receipt["pre_bound_count"] - fetch_receipt["retained_count"]
        ):
            raise ValueError("promoted packet query fetch receipt is inconsistent")
        expected_relaxation_paths = [
            attempt["path"] for attempt in fetched_relaxations
        ]
        if query.get("relaxation_policy") == "none":
            if expected_relaxation_paths or "relaxation_attempts" in query:
                raise ValueError("promoted packet query policy forbids relaxation")
        elif expected_relaxation_paths:
            expected_path = query["path"]
            for observed_path in expected_relaxation_paths:
                expected_path = a6.relax_query(expected_path)
                if expected_path is None or observed_path != expected_path:
                    raise ValueError(
                        "promoted packet query relaxation path is not reproducible"
                    )
            if relaxation_attempts != expected_relaxation_paths:
                raise ValueError("promoted packet query relaxation receipts differ")
        elif "relaxation_attempts" in query:
            raise ValueError("promoted packet has an empty relaxation path receipt")


def _validate_intent(intent: dict[str, Any]) -> None:
    if intent.get("planner") != a6.QO_PLANNER_VERSION:
        raise ValueError("promoted record intent planner changed")
    for field in ("resource_types", "search_terms"):
        value = intent.get(field)
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ValueError(f"promoted record intent {field} is invalid")
    windows = intent.get("date_windows")
    if not isinstance(windows, list) or any(
        not isinstance(window, dict)
        or set(window) != {"start", "end", "source"}
        or not all(
            window.get(field) is None or isinstance(window.get(field), str)
            for field in ("start", "end")
        )
        or not isinstance(window.get("source"), str)
        for window in windows
    ):
        raise ValueError("promoted record intent date_windows are invalid")
    if intent.get("temporal_policy") not in {"first_last", "recent"}:
        raise ValueError("promoted record intent temporal_policy is invalid")
    current_date = intent.get("current_date")
    if current_date is not None and not isinstance(current_date, str):
        raise ValueError("promoted record intent current_date is invalid")


def _validate_question_only_semantics(
    record: dict[str, Any], intent: dict[str, Any], packet: dict[str, Any]
) -> frozenset[str]:
    safe_row = {
        field: record[field]
        for field in ("question_id", "question", "patient_fhir_id", "assumption")
    }
    expected_intent = a6.qo_infer_intent(safe_row)
    if intent != expected_intent:
        raise ValueError("promoted record intent is not question-only reproducible")
    expected_plan = a6.build_search_plan(
        safe_row,
        expected_intent,
        count=100,
        features={"micro-vocab"},
    )
    actual_plan = packet.get("source_queries")
    if not isinstance(actual_plan, list) or len(actual_plan) != len(expected_plan):
        raise ValueError("promoted packet source plan is not question-only reproducible")
    receipt_fields = {"fetch_receipt", "relaxation_attempts"}
    for actual, expected in zip(actual_plan, expected_plan, strict=True):
        if not isinstance(actual, dict):
            raise ValueError("promoted packet source plan is invalid")
        actual_base_fields = set(actual) - receipt_fields
        if actual_base_fields != set(expected) or {
            key: actual[key] for key in actual_base_fields
        } != expected:
            raise ValueError("promoted packet source plan is not question-only reproducible")
    return frozenset(item["resource_type"] for item in expected_plan)


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if set(manifest) != _MANIFEST_FIELDS:
        raise ValueError("promoted packet manifest fields changed")
    if manifest.get("kind") != "a6_query_aware_packet_manifest":
        raise ValueError("unsupported promoted packet manifest kind")
    if not isinstance(manifest.get("created_at"), str) or not manifest["created_at"]:
        raise ValueError("promoted packet manifest has no created_at")
    config = manifest.get("config")
    if not isinstance(config, dict):
        raise ValueError("promoted packet manifest has no config")
    if set(config) != _MANIFEST_CONFIG_FIELDS:
        raise ValueError("promoted packet manifest config fields changed")
    recipe = config.get("evidence_recipe")
    if not isinstance(recipe, dict) or recipe.get("id") != a6.PROMOTED_EVIDENCE_RECIPE:
        raise ValueError("manifest does not identify the promoted evidence recipe")
    if recipe.get("features") != ["micro-vocab"]:
        raise ValueError("manifest promoted recipe feature set changed")
    if recipe.get("status") != "promoted_on_qt4_valid374":
        raise ValueError("manifest promoted recipe status changed")
    if recipe.get("promotion_result") != "docs/results/QT4_VALID374_RESULT.md":
        raise ValueError("manifest promoted recipe result changed")
    if config.get("features") != ["micro-vocab"]:
        raise ValueError("manifest feature set is not the promoted vocabulary arm")
    if config.get("planner") != "question-only":
        raise ValueError("manifest planner is not question-only")
    if config.get("planner_version") != a6.QO_PLANNER_VERSION:
        raise ValueError("manifest question-only planner version changed")
    if config.get("max_total_resources") != a6.A6A_MAX_TOTAL_RESOURCES:
        raise ValueError("manifest promoted resource bound changed")
    if config.get("max_packet_chars") != a6.A6A_MAX_PACKET_CHARS:
        raise ValueError("manifest promoted packet character bound changed")
    if config.get("plan_only") is not False:
        raise ValueError("plan-only packets are ineligible for A11")
    if config.get("reference_traversal") is not None:
        raise ValueError("promoted V manifest unexpectedly enables traversal")
    if config.get("count") != 100:
        raise ValueError("manifest promoted query count changed")
    limit = config.get("limit")
    if limit is not None and (
        not isinstance(limit, int) or isinstance(limit, bool) or limit < 1
    ):
        raise ValueError("manifest limit is invalid")
    split = config.get("split")
    if split is not None and (not isinstance(split, str) or not split):
        raise ValueError("manifest split is invalid")

    for field in ("input", "output"):
        receipt = manifest.get(field)
        if not isinstance(receipt, dict) or set(receipt) != {"path", "sha256"}:
            raise ValueError(f"manifest {field} receipt fields changed")
        if (
            not isinstance(receipt.get("path"), str)
            or not receipt["path"]
            or not _is_sha256(receipt.get("sha256"))
        ):
            raise ValueError(f"manifest {field} receipt is invalid")
    question_spec = config.get("question_spec")
    if question_spec is not None and (
        not isinstance(question_spec, dict)
        or set(question_spec) != {"path", "sha256"}
        or not isinstance(question_spec.get("path"), str)
        or not question_spec["path"]
        or not _is_sha256(question_spec.get("sha256"))
    ):
        raise ValueError("manifest question_spec receipt fields changed")

    recipe = config["evidence_recipe"]
    if set(recipe) != {"id", "status", "features", "promotion_result"}:
        raise ValueError("manifest evidence recipe fields changed")
    vocabulary = config.get("micro_vocabulary")
    if not isinstance(vocabulary, dict) or set(vocabulary) != {
        "version",
        "code_text_terms",
    }:
        raise ValueError("manifest microbiology vocabulary receipt changed")
    if vocabulary.get("version") != a6.MICRO_VOCABULARY_VERSION:
        raise ValueError("manifest microbiology vocabulary version changed")
    if vocabulary.get("code_text_terms") != list(a6.MICRO_CODE_TEXT_TERMS):
        raise ValueError("manifest microbiology vocabulary terms changed")
    dispatcher = config.get("micro_dispatcher")
    if not isinstance(dispatcher, dict) or set(dispatcher) != {
        "version",
        "question_terms",
    }:
        raise ValueError("manifest microbiology dispatcher receipt changed")
    if dispatcher.get("version") != a6.MICRO_DISPATCHER_VERSION:
        raise ValueError("manifest microbiology dispatcher version changed")
    if dispatcher.get("question_terms") != list(a6.MICRO_QUESTION_TERMS):
        raise ValueError("manifest microbiology dispatcher terms changed")

    questions = manifest.get("questions")
    if (
        not isinstance(questions, int)
        or isinstance(questions, bool)
        or questions < 1
        or questions > A11_MAX_PROMOTED_QUESTIONS
    ):
        raise ValueError("manifest question count is outside the A11 bound")
    packet_hashes = manifest.get("packet_hashes")
    if not isinstance(packet_hashes, dict) or not all(
        isinstance(key, str) and key and _is_sha256(value)
        for key, value in packet_hashes.items()
    ):
        raise ValueError("manifest packet_hashes are invalid")


def _load_records(packet_bytes: bytes) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    stream = io.BytesIO(packet_bytes)
    line_number = 0
    while True:
        line = stream.readline(A11_MAX_PROMOTED_RECORD_BYTES + 1)
        if not line:
            break
        line_number += 1
        if len(line) > A11_MAX_PROMOTED_RECORD_BYTES:
            raise ValueError(f"packet JSONL line {line_number} exceeds the A11 bound")
        if not line.strip():
            continue
        value = _json_loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"packet JSONL line {line_number} is not an object")
        raw_question_id = value.get("question_id")
        if not isinstance(raw_question_id, str) or not raw_question_id:
            raise ValueError(f"packet JSONL line {line_number} has no string question_id")
        if raw_question_id in records:
            raise ValueError(f"duplicate promoted packet question_id: {raw_question_id}")
        records[raw_question_id] = value
        if len(records) > A11_MAX_PROMOTED_QUESTIONS:
            raise ValueError("promoted packet JSONL exceeds the A11 question bound")
    if not records:
        raise ValueError("promoted packet JSONL is empty")
    return records


def _validate_record(
    record: dict[str, Any],
    *,
    question_id: str,
    manifest_packet_hash: Any,
) -> dict[str, Any]:
    unexpected = set(record) - _RECORD_FIELDS
    missing = _RECORD_FIELDS - set(record)
    if unexpected or missing:
        raise ValueError(
            "promoted record fields do not match compile_evidence.py schema: "
            f"missing={sorted(missing)} unexpected={sorted(unexpected)}"
        )

    packet = record.get("packet")
    if not isinstance(packet, dict):
        raise ValueError("promoted record has no packet object")
    nonclinical_record = {
        key: value for key, value in record.items() if key != "packet"
    }
    nonclinical_record["packet"] = {
        key: value for key, value in packet.items() if key != "resources"
    }
    _reject_forbidden_input(nonclinical_record)

    question = record.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("promoted record has no question")
    if record.get("question_id") != question_id:
        raise ValueError("promoted record question_id is inconsistent")
    if not isinstance(record.get("assumption"), str):
        raise ValueError("promoted record assumption is not a string")
    patient_ref = _patient_ref(record.get("patient_fhir_id"))
    question_plan = plan_question(question)
    planned_root_types = _planned_root_types(question_plan)

    intent = record.get("intent")
    if not isinstance(intent, dict) or set(intent) != _INTENT_FIELDS:
        raise ValueError("promoted record intent fields changed")
    _validate_intent(intent)

    if set(packet) != _PACKET_FIELDS:
        raise ValueError("promoted packet metadata fields changed")
    if packet.get("kind") != "a6a_question_only_packet":
        raise ValueError("promoted packet kind changed")
    if packet.get("planner") != a6.QO_PLANNER_VERSION:
        raise ValueError("promoted packet planner version changed")
    if packet.get("features") != ["micro-vocab"]:
        raise ValueError("A11 requires a micro-dispatched promoted packet")
    if packet.get("plan_only") is not False:
        raise ValueError("plan-only promoted packet is ineligible for A11")
    if "reference_traversal" in packet:
        raise ValueError("promoted V packet unexpectedly contains traversal")
    _validate_nonclinical_packet_metadata(packet)
    selected_resource_types = _validate_question_only_semantics(record, intent, packet)

    resources = packet.get("resources")
    if not isinstance(resources, list):
        raise ValueError("promoted packet resources are not a list")
    if packet.get("resource_count") != len(resources):
        raise ValueError("promoted packet resource_count is inconsistent")
    if len(resources) > a6.A6A_MAX_TOTAL_RESOURCES:
        raise ValueError("promoted packet exceeds the registered resource bound")
    resource_ids = sorted(_resource_ref(resource) for resource in resources)
    if len(resource_ids) != len(set(resource_ids)):
        raise ValueError("promoted packet contains duplicate resource IDs")
    if packet.get("source_resource_ids") != resource_ids:
        raise ValueError("promoted packet source_resource_ids are inconsistent")
    bounds = packet.get("bounds")
    if not isinstance(bounds, dict) or set(bounds) != _BOUND_FIELDS:
        raise ValueError("promoted packet has no live bounds receipt")
    if bounds.get("max_total_resources") != a6.A6A_MAX_TOTAL_RESOURCES:
        raise ValueError("promoted packet resource bound changed")
    if bounds.get("max_packet_chars") != a6.A6A_MAX_PACKET_CHARS:
        raise ValueError("promoted packet character bound changed")
    char_count = bounds.get("char_count")
    if (
        not isinstance(char_count, int)
        or isinstance(char_count, bool)
        or char_count < 0
        or char_count > a6.A6A_MAX_PACKET_CHARS
    ):
        raise ValueError("promoted packet character count is invalid")
    count_fields = ("input_count", "kept_count", "dropped_count")
    if not all(
        isinstance(bounds.get(field), int)
        and not isinstance(bounds.get(field), bool)
        and bounds[field] >= 0
        for field in count_fields
    ):
        raise ValueError("promoted packet bounds counts are invalid")
    if (
        bounds["kept_count"] != len(resources)
        or bounds["dropped_count"]
        != bounds["input_count"] - bounds["kept_count"]
    ):
        raise ValueError("promoted packet bounds counts are inconsistent")
    if not isinstance(bounds.get("char_budget_hit"), bool):
        raise ValueError("promoted packet char_budget_hit is invalid")
    if bounds.get("temporal_policy") != intent.get("temporal_policy"):
        raise ValueError("promoted packet temporal policy is inconsistent")
    recomputed_char_count = sum(
        len(_canonical_text(resource)) for resource in resources
    )
    if char_count != recomputed_char_count:
        raise ValueError("promoted packet character count is inconsistent")
    root_fetch = packet["root_fetch_receipt"]
    if (
        root_fetch["pre_bound_count"] != bounds["input_count"]
        or root_fetch["retained_count"] != bounds["kept_count"]
        or root_fetch["dropped_count"] != bounds["dropped_count"]
    ):
        raise ValueError("promoted packet root fetch and bounds receipts differ")

    packet_hash = packet.get("sha256")
    hash_input = {key: value for key, value in packet.items() if key != "sha256"}
    recomputed_packet_hash = _sha256(_canonical_bytes(hash_input))
    if not _is_sha256(packet_hash) or packet_hash != recomputed_packet_hash:
        raise ValueError("promoted packet internal sha256 is invalid")
    if manifest_packet_hash != packet_hash:
        raise ValueError("promoted packet sha256 does not match manifest")

    _validate_patient_scope(
        packet,
        patient_ref,
        selected_resource_types=selected_resource_types,
    )
    root_refs = _root_refs(packet, patient_ref, planned_root_types)

    return {
        "record": record,
        "packet": packet,
        "packet_sha256": packet_hash,
        "patient_ref": patient_ref,
        "root_refs": root_refs,
        "question_plan": question_plan,
        "record_sha256": _sha256(_canonical_bytes(record)),
    }


class PromotedBundle:
    """One immutable, once-verified promoted JSONL/manifest pair."""

    def __init__(
        self,
        *,
        records: dict[str, dict[str, Any]],
        manifest_sha256: str,
        packet_file_sha256: str,
        dependency_hashes: dict[str, str],
        _factory_token: object | None = None,
    ) -> None:
        if _factory_token is not _BUNDLE_FACTORY_TOKEN:
            raise TypeError("use load_promoted_bundle to construct a verified bundle")
        self._records = records
        self._manifest_sha256 = manifest_sha256
        self._packet_file_sha256 = packet_file_sha256
        self._dependency_hashes = dependency_hashes
        self.question_ids = tuple(records)

    def load(self, question_id: str) -> dict[str, Any]:
        """Return one exact V payload from the already verified bundle."""

        if question_id not in self._records:
            raise ValueError(f"promoted packet question_id not found: {question_id}")
        validated = self._records[question_id]
        record = validated["record"]
        packet = validated["packet"]

        rendered_payload = codex_harness.render_model_visible_packet(
            copy.deepcopy(packet)
        )
        model_payload = _json_loads(rendered_payload)
        rendered_bytes = rendered_payload.encode("utf-8")

        return {
            "schema_version": ADAPTER_VERSION,
            "question_id": question_id,
            "question": record["question"],
            "patient_fhir_id": record["patient_fhir_id"],
            "patient_ref": validated["patient_ref"],
            "assumption": record["assumption"],
            "intent": copy.deepcopy(record["intent"]),
            "question_plan": copy.deepcopy(validated["question_plan"]),
            "packet": copy.deepcopy(packet),
            "root_refs": list(validated["root_refs"]),
            "v_model_payload": model_payload,
            "v_model_payload_json": rendered_payload,
            "integrity": {
                "adapter_sha256": self._dependency_hashes[
                    "a11_packet_adapter.py"
                ],
                "dependency_sha256": dict(self._dependency_hashes),
                "manifest_sha256": self._manifest_sha256,
                "packet_file_sha256": self._packet_file_sha256,
                "record_sha256": validated["record_sha256"],
                "packet_sha256": validated["packet_sha256"],
                "model_payload_sha256": _sha256(rendered_bytes),
                "model_payload_utf8_bytes": len(rendered_bytes),
                "root_refs_sha256": _sha256(
                    _canonical_bytes(validated["root_refs"])
                ),
            },
        }


def load_promoted_bundle(
    packet_path: Path,
    manifest_path: Path,
    *,
    expected_manifest_sha256: str,
) -> PromotedBundle:
    """Read and validate a promoted packet corpus exactly once."""

    if not _is_sha256(expected_manifest_sha256):
        raise ValueError("expected_manifest_sha256 must be a lowercase sha256")
    packet_path = packet_path.resolve()
    manifest_path = manifest_path.resolve()
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = _sha256(manifest_bytes)
    if manifest_sha256 != expected_manifest_sha256:
        raise ValueError("promoted packet manifest does not match the pinned sha256")
    manifest = _json_loads(manifest_bytes)
    if not isinstance(manifest, dict):
        raise ValueError("promoted packet manifest is not an object")
    _reject_forbidden_input(manifest, "manifest")
    _validate_manifest(manifest)

    if packet_path.stat().st_size > A11_MAX_PROMOTED_FILE_BYTES:
        raise ValueError("promoted packet file exceeds the A11 byte bound")
    packet_bytes = packet_path.read_bytes()
    if len(packet_bytes) > A11_MAX_PROMOTED_FILE_BYTES:
        raise ValueError("promoted packet file exceeds the A11 byte bound")
    actual_file_hash = _sha256(packet_bytes)
    if manifest["output"]["sha256"] != actual_file_hash:
        raise ValueError("promoted packet file does not match manifest output sha256")
    records = _load_records(packet_bytes)
    manifest_hashes = manifest.get("packet_hashes")
    if set(records) != set(manifest_hashes):
        raise ValueError("packet JSONL question IDs do not match manifest packet hashes")
    if manifest.get("questions") != len(records):
        raise ValueError("manifest question count does not match packet JSONL")

    validated_records = {
        question_id: _validate_record(
            record,
            question_id=question_id,
            manifest_packet_hash=manifest_hashes[question_id],
        )
        for question_id, record in records.items()
    }
    repo = Path(__file__).resolve().parent
    dependency_hashes = {
        filename: _sha256((repo / filename).read_bytes())
        for filename in _DEPENDENCY_FILES
    }
    return PromotedBundle(
        records=validated_records,
        manifest_sha256=manifest_sha256,
        packet_file_sha256=actual_file_hash,
        dependency_hashes=dependency_hashes,
        _factory_token=_BUNDLE_FACTORY_TOKEN,
    )


def load_promoted_record(
    packet_path: Path,
    manifest_path: Path,
    question_id: str,
    *,
    expected_manifest_sha256: str,
) -> dict[str, Any]:
    """Convenience loader for a single micro-dispatched promoted packet."""

    return load_promoted_bundle(
        packet_path,
        manifest_path,
        expected_manifest_sha256=expected_manifest_sha256,
    ).load(question_id)
