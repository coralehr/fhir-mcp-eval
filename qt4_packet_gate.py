#!/usr/bin/env python3
"""Read-only, zero-model readiness gate for the QT-4 packet experiment.

The report compares frozen A6a, QT-4V, and QT-4T packet files against an
explicit question specification. Gold FHIR IDs are read only for offline
evaluation metrics; they are never copied into packets or model prompts.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import re
import sys
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from codex_harness import build_prompt


SCHEMA_VERSION = "qt4-zero-model-packet-gate-v1"
ARM_NAMES = ("a6a", "qt4v", "qt4t")
V_FEATURES = ("micro-vocab",)
T_FEATURES = ("micro-traversal", "micro-vocab")
PATH_FAMILIES = (
    "Observation.hasMember",
    "Observation.specimen",
    "DiagnosticReport.result",
    "DiagnosticReport.specimen",
)
PATH_STATUSES = (
    "fetched",
    "already_present",
    "missing",
    "max_resources",
    "max_serialized_bytes",
)
FROZEN_TRAVERSAL_VERSION = "micro-traversal-v1"
FROZEN_TRAVERSAL_KIND = "bounded_exact_reference_traversal"
FROZEN_TRAVERSAL_LIMITS = {
    "max_depth": 2,
    "max_resources": 24,
    "max_serialized_bytes": 24_000,
    "max_path_receipts": 48,
    "max_path_receipt_bytes": 12_000,
}
MICRO_DISPATCH_VERSION = "micro-dispatch-v1"
MICRO_DISPATCH_TERMS = (
    "microbiolog",
    "microbial",
    "culture",
    "specimen",
    "organism",
    "smear",
    "gram stain",
    "screen",
)
MICRO_VOCABULARY_VERSION = "micro-v1"
MICRO_CODE_TEXT_TERMS = ("culture", "gram stain", "screen", "smear")
PROMPT_METADATA_FIELDS = (
    "question_id",
    "question",
    "question_with_context",
    "patient_fhir_id",
    "assumption",
)
FORBIDDEN_PACKET_KEYS = frozenset(
    {
        "true_answer",
        "true_fhir_ids",
        "gold",
        "gold_answer",
        "expected_answer",
        "proc_query",
        "sql_query",
    }
)
_FHIR_REFERENCE_RE = re.compile(
    r"(?P<type>[A-Za-z][A-Za-z0-9]*)/(?P<id>[A-Za-z0-9\-.]{1,64})"
)
_RECEIPT_PATH_RE = re.compile(
    r"(?P<source>Observation|DiagnosticReport)\."
    r"(?P<field>hasMember|specimen|result)"
    r"(?:\[(?P<index>\d+)\])?\.reference"
)
_ALLOWED_RECEIPT_TARGETS = {
    ("Observation", "hasMember"): "Observation",
    ("Observation", "specimen"): "Specimen",
    ("DiagnosticReport", "result"): "Observation",
    ("DiagnosticReport", "specimen"): "Specimen",
}
_FETCH_ATTEMPT_STATUSES = frozenset({"fetched", "missing", "max_serialized_bytes"})


class GateInputError(ValueError):
    """Raised when an input cannot represent one packet per scheduled ID."""


@dataclass(frozen=True)
class GateExpectations:
    expected_total: int | None = None
    expected_micro: int | None = None
    expected_non_micro: int | None = None
    min_vocab_gold_gain: int = 1
    min_traversal_gold_gain: int = 1


@dataclass(frozen=True)
class QuestionSpec:
    scheduled_ids: tuple[str, ...]
    microbiology_ids: frozenset[str]
    gold_ids: dict[str, frozenset[str]]
    input_rows: dict[str, dict[str, Any]]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_bytes(value: Any) -> int:
    return len(_canonical_json(value).encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _question_id(value: Any, *, source: Path) -> str:
    question_id = str(value or "").strip()
    if not question_id:
        raise GateInputError(f"missing question_id in {source}")
    return question_id


def _records_from_json_value(value: Any, *, source: Path) -> list[dict[str, Any]]:
    if isinstance(value, list):
        records = value
    elif isinstance(value, dict) and isinstance(value.get("records"), list):
        records = value["records"]
    elif isinstance(value, dict) and "question_id" in value:
        records = [value]
    elif isinstance(value, dict):
        records = []
        for question_id, item in value.items():
            if isinstance(item, dict) and "question_id" in item:
                records.append(item)
            elif isinstance(item, dict) and "packet" in item:
                records.append({"question_id": question_id, **item})
            else:
                records.append({"question_id": question_id, "packet": item})
    else:
        raise GateInputError(f"expected JSON records in {source}")
    if not all(isinstance(record, dict) for record in records):
        raise GateInputError(f"all packet records must be JSON objects in {source}")
    return records


def load_packet_records(path: Path) -> dict[str, dict[str, Any]]:
    """Load a JSON array/map or JSONL packet file, rejecting duplicate IDs."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise GateInputError(f"empty packet file: {path}")
    try:
        records = _records_from_json_value(json.loads(text), source=path)
    except json.JSONDecodeError:
        records = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GateInputError(
                    f"invalid JSONL in {path} line {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise GateInputError(
                    f"packet record in {path} line {line_number} is not an object"
                )
            records.append(record)

    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        question_id = _question_id(record.get("question_id"), source=path)
        if question_id in by_id:
            raise GateInputError(f"duplicate question_id {question_id!r} in {path}")
        if not isinstance(record.get("packet"), dict):
            raise GateInputError(
                f"question_id {question_id!r} has no packet object in {path}"
            )
        by_id[question_id] = record
    return by_id


def _parse_gold_ids(value: Any) -> frozenset[str]:
    if value is None or value == "":
        return frozenset()
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"nan", "none"}:
            return frozenset()
        try:
            value = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise GateInputError(
                    "true_fhir_ids is not a JSON/Python mapping"
                ) from exc
    if not isinstance(value, dict):
        raise GateInputError("true_fhir_ids must be a resource-type to ID mapping")

    flattened: set[str] = set()
    for resource_type, raw_ids in value.items():
        if raw_ids is None:
            continue
        ids = [raw_ids] if isinstance(raw_ids, str) else raw_ids
        if not isinstance(ids, (list, tuple, set, frozenset)):
            raise GateInputError(f"true_fhir_ids[{resource_type!r}] must be a list")
        for raw_id in ids:
            resource_id = str(raw_id or "").strip()
            if not resource_id:
                continue
            if "/" in resource_id:
                flattened.add(resource_id)
            else:
                flattened.add(f"{resource_type}/{resource_id}")
    return frozenset(flattened)


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _row_is_microbiology(row: dict[str, Any]) -> bool:
    if "is_microbiology" in row and str(row.get("is_microbiology") or "").strip():
        return _is_truthy(row.get("is_microbiology"))
    if str(row.get("main_table_name") or "").strip().lower() == "microbiologyevents":
        return True
    return str(row.get("stratum") or "").strip().lower() in {
        "microbiology",
        "microbiologyevents",
    }


def _spec_from_rows(rows: Iterable[dict[str, Any]], *, source: Path) -> QuestionSpec:
    scheduled: list[str] = []
    seen: set[str] = set()
    microbiology: set[str] = set()
    gold: dict[str, frozenset[str]] = {}
    input_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        row = dict(row)
        question_id = _question_id(row.get("question_id"), source=source)
        if question_id in seen:
            raise GateInputError(f"duplicate question_id {question_id!r} in {source}")
        seen.add(question_id)
        scheduled.append(question_id)
        if _row_is_microbiology(row):
            microbiology.add(question_id)
        gold[question_id] = _parse_gold_ids(row.get("true_fhir_ids"))
        input_rows[question_id] = row
    if not scheduled:
        raise GateInputError(f"question specification has no rows: {source}")
    return QuestionSpec(
        scheduled_ids=tuple(sorted(scheduled)),
        microbiology_ids=frozenset(microbiology),
        gold_ids=gold,
        input_rows=input_rows,
    )


def load_question_spec(path: Path) -> QuestionSpec:
    """Load scheduled IDs, microbiology stratum, and evaluation-only gold IDs.

    CSV input uses `question_id`, `true_fhir_ids`, and either
    `main_table_name=microbiologyevents`, `stratum=microbiology`, or an explicit
    `is_microbiology` boolean. JSON may use the same row format or the explicit
    `scheduled_question_ids` / `microbiology_question_ids` / `gold_fhir_ids`
    structure.
    """
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return _spec_from_rows(csv.DictReader(handle), source=path)

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GateInputError(f"invalid question specification JSON: {path}") from exc
    if isinstance(value, list):
        if not all(isinstance(row, dict) for row in value):
            raise GateInputError(f"question specification rows must be objects: {path}")
        return _spec_from_rows(value, source=path)
    if isinstance(value, dict) and isinstance(value.get("questions"), list):
        return _spec_from_rows(value["questions"], source=path)
    if not isinstance(value, dict):
        raise GateInputError(f"unsupported question specification: {path}")

    raw_scheduled = value.get("scheduled_question_ids")
    raw_micro = value.get("microbiology_question_ids", [])
    raw_gold = value.get("gold_fhir_ids", {})
    if not isinstance(raw_scheduled, list) or not isinstance(raw_micro, list):
        raise GateInputError(
            "explicit JSON spec requires scheduled_question_ids and microbiology_question_ids lists"
        )
    if not isinstance(raw_gold, dict):
        raise GateInputError("explicit JSON spec gold_fhir_ids must be a mapping")
    rows = []
    micro = {str(question_id) for question_id in raw_micro}
    for question_id in raw_scheduled:
        normalized = str(question_id)
        rows.append(
            {
                "question_id": normalized,
                "is_microbiology": normalized in micro,
                "true_fhir_ids": raw_gold.get(normalized, {}),
            }
        )
    spec = _spec_from_rows(rows, source=path)
    unknown_micro = micro - set(spec.scheduled_ids)
    if unknown_micro:
        raise GateInputError(
            f"microbiology IDs are not scheduled: {sorted(unknown_micro)}"
        )
    return spec


def _packet(record: dict[str, Any] | None) -> dict[str, Any]:
    if not record:
        return {}
    packet = record.get("packet")
    return packet if isinstance(packet, dict) else {}


def _resources(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    resources = _packet(record).get("resources")
    if not isinstance(resources, list):
        return []
    return [resource for resource in resources if isinstance(resource, dict)]


def _resource_id(resource: dict[str, Any]) -> str | None:
    resource_type = resource.get("resourceType")
    resource_id = resource.get("id")
    if (
        isinstance(resource_type, str)
        and resource_type
        and isinstance(resource_id, str)
        and resource_id
    ):
        return f"{resource_type}/{resource_id}"
    return None


def _packet_resource_ids(record: dict[str, Any] | None) -> frozenset[str]:
    return frozenset(
        resource_id
        for resource in _resources(record)
        if (resource_id := _resource_id(resource)) is not None
    )


def _features(record: dict[str, Any] | None) -> tuple[str, ...]:
    features = _packet(record).get("features")
    if not isinstance(features, list):
        return ()
    return tuple(sorted(str(feature) for feature in features))


def _effective_prompt_row(
    input_row: dict[str, Any], record: dict[str, Any]
) -> dict[str, Any]:
    """Reproduce the harness's exact frozen-input then packet-row overlay."""
    return {**input_row, **record}


def _prompt(input_row: dict[str, Any], record: dict[str, Any]) -> str:
    return build_prompt(_effective_prompt_row(input_row, record), mode="packet")


def _frozen_metadata_prompt(
    input_row: dict[str, Any], record: dict[str, Any]
) -> str:
    """Render the record's packet with metadata sourced only from frozen input."""
    return build_prompt({**input_row, "packet": record["packet"]}, mode="packet")


def _prompt_metadata_issues(
    input_row: dict[str, Any], record: dict[str, Any] | None
) -> list[str]:
    """Return field names only so the integrity report does not copy row values."""
    if not record:
        return ["packet_record_missing"]
    if not isinstance(record.get("packet"), dict):
        return ["packet_missing"]
    effective = _effective_prompt_row(input_row, record)
    issues = [
        f"metadata_changed:{field}"
        for field in PROMPT_METADATA_FIELDS
        if effective.get(field) != input_row.get(field)
    ]
    try:
        if _prompt(input_row, record) != _frozen_metadata_prompt(input_row, record):
            issues.append("effective_prompt_differs_from_frozen_metadata_render")
    except (TypeError, ValueError):
        issues.append("prompt_render_rejected")
    return issues


def _recall_metrics(
    records: dict[str, dict[str, Any]],
    spec: QuestionSpec,
    question_ids: Iterable[str],
) -> dict[str, Any]:
    recalls: list[float] = []
    gold_total = 0
    retrieved_gold_total = 0
    any_count = 0
    all_count = 0
    for question_id in question_ids:
        gold = spec.gold_ids.get(question_id, frozenset())
        if not gold:
            continue
        retrieved = _packet_resource_ids(records.get(question_id))
        matched = gold & retrieved
        recalls.append(len(matched) / len(gold))
        gold_total += len(gold)
        retrieved_gold_total += len(matched)
        any_count += int(bool(matched))
        all_count += int(gold <= retrieved)
    denominator = len(recalls)
    return {
        "questions_with_gold": denominator,
        "gold_id_occurrences": gold_total,
        "retrieved_gold_id_occurrences": retrieved_gold_total,
        "macro_recall": sum(recalls) / denominator if denominator else None,
        "id_weighted_recall": retrieved_gold_total / gold_total if gold_total else None,
        "any_coverage_count": any_count,
        "any_coverage": any_count / denominator if denominator else None,
        "all_coverage_count": all_count,
        "all_coverage": all_count / denominator if denominator else None,
    }


def _gold_change(
    baseline: dict[str, dict[str, Any]],
    treatment: dict[str, dict[str, Any]],
    spec: QuestionSpec,
    question_ids: Iterable[str],
) -> dict[str, Any]:
    gained: list[tuple[str, str]] = []
    lost: list[tuple[str, str]] = []
    for question_id in sorted(question_ids):
        gold = spec.gold_ids.get(question_id, frozenset())
        before = gold & _packet_resource_ids(baseline.get(question_id))
        after = gold & _packet_resource_ids(treatment.get(question_id))
        gained.extend(
            (question_id, resource_id) for resource_id in sorted(after - before)
        )
        lost.extend(
            (question_id, resource_id) for resource_id in sorted(before - after)
        )

    def group(items: list[tuple[str, str]]) -> list[dict[str, Any]]:
        by_question: dict[str, list[str]] = {}
        for question_id, resource_id in items:
            by_question.setdefault(question_id, []).append(resource_id)
        return [
            {"question_id": question_id, "resource_ids": resource_ids}
            for question_id, resource_ids in sorted(by_question.items())
        ]

    return {
        "gold_id_occurrences_gained": len(gained),
        "questions_with_gain": len({question_id for question_id, _ in gained}),
        "gained_by_question": group(gained),
        "gold_id_occurrences_lost": len(lost),
        "questions_with_loss": len({question_id for question_id, _ in lost}),
        "lost_by_question": group(lost),
    }


def _path_family(path: Any) -> str:
    normalized = re.sub(r"\[\d+\]", "", str(path or ""))
    if normalized.endswith(".reference"):
        normalized = normalized[: -len(".reference")]
    return normalized or "unknown"


def _forbidden_packet_paths(value: Any, *, path: str = "packet") -> list[str]:
    """Return key paths only; never copy benchmark values into the report."""
    found: list[str] = []
    if isinstance(value, dict):
        for raw_key, child in sorted(value.items(), key=lambda item: str(item[0])):
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if key.lower() in FORBIDDEN_PACKET_KEYS:
                found.append(child_path)
            found.extend(_forbidden_packet_paths(child, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_forbidden_packet_paths(child, path=f"{path}[{index}]"))
    return found


def _query_fetch_issues(record: dict[str, Any] | None) -> list[str]:
    """Validate that every root query completed with an auditable safe receipt."""
    packet = _packet(record)
    issues: list[str] = []
    queries = packet.get("source_queries")
    if not isinstance(queries, list) or not queries:
        return ["source_queries_missing"]
    for index, query in enumerate(queries):
        prefix = f"source_queries[{index}]"
        if not isinstance(query, dict):
            issues.append(f"{prefix}_not_object")
            continue
        receipt = query.get("fetch_receipt")
        if not isinstance(receipt, dict):
            issues.append(f"{prefix}_fetch_receipt_missing")
            continue
        if receipt.get("status") != "ok" or receipt.get("error") is not None:
            issues.append(f"{prefix}_fetch_not_ok")
        counts = {
            key: _nonnegative_int(receipt.get(key))
            for key in ("pre_bound_count", "retained_count", "dropped_count")
        }
        if any(value is None for value in counts.values()):
            issues.append(f"{prefix}_fetch_counts_invalid")
        elif counts["pre_bound_count"] != counts["retained_count"] + counts["dropped_count"]:
            issues.append(f"{prefix}_fetch_count_arithmetic")
        initial = receipt.get("initial_result_count")
        if initial is not None and _nonnegative_int(initial) is None:
            issues.append(f"{prefix}_initial_result_count_invalid")
        attempts = receipt.get("relaxation_attempts")
        if not isinstance(attempts, list):
            issues.append(f"{prefix}_relaxation_attempts_invalid")
        else:
            for attempt_index, attempt in enumerate(attempts):
                if (
                    not isinstance(attempt, dict)
                    or not isinstance(attempt.get("path"), str)
                    or _nonnegative_int(attempt.get("result_count")) is None
                ):
                    issues.append(
                        f"{prefix}_relaxation_attempt[{attempt_index}]_invalid"
                    )
        if query.get("relaxation_policy") == "none" and attempts:
            issues.append(f"{prefix}_forbidden_relaxation")

    root_receipt = packet.get("root_fetch_receipt")
    if not isinstance(root_receipt, dict):
        issues.append("root_fetch_receipt_missing")
    else:
        counts = {
            key: _nonnegative_int(root_receipt.get(key))
            for key in ("pre_bound_count", "retained_count", "dropped_count")
        }
        if any(value is None for value in counts.values()):
            issues.append("root_fetch_counts_invalid")
        elif counts["pre_bound_count"] != counts["retained_count"] + counts["dropped_count"]:
            issues.append("root_fetch_count_arithmetic")
    if any(
        resource.get("resourceType") == "OperationOutcome"
        for resource in _resources(record)
    ):
        issues.append("operation_outcome_in_model_evidence")
    return sorted(set(issues))


def _question_text(
    record: dict[str, Any] | None, input_row: dict[str, Any]
) -> str | None:
    if not record:
        return None
    effective = _effective_prompt_row(input_row, record)
    question = effective.get("question") or effective.get("question_with_context")
    if not isinstance(question, str):
        return None
    return question if question.strip() else None


def _micro_dispatches(question: str | None) -> bool:
    lowered = str(question or "").lower()
    return any(term in lowered for term in MICRO_DISPATCH_TERMS)


def _source_queries(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    queries = _packet(record).get("source_queries")
    if not isinstance(queries, list):
        return []
    return [query for query in queries if isinstance(query, dict)]


def _observation_query_signature(
    item: dict[str, Any], *, expected_term: str | None
) -> tuple[str, tuple[tuple[str, str], ...]] | None:
    """Normalize only code:text, retaining every frozen patient/date/sort param."""
    if item.get("resource_type") != "Observation":
        return None
    path = item.get("path")
    if not isinstance(path, str) or "?" not in path:
        return None
    resource_type, query = path.split("?", 1)
    if resource_type != "Observation" or "#" in query:
        return None
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    code_terms = [value for key, value in pairs if key == "code:text"]
    if expected_term is not None and code_terms != [expected_term]:
        return None
    return resource_type, tuple(
        (key, value) for key, value in pairs if key != "code:text"
    )


def _micro_vocab_query_issues(
    baseline_record: dict[str, Any] | None,
    treatment_record: dict[str, Any] | None,
) -> list[str]:
    """Validate the term-major four-query union against the A6a base signatures."""
    baseline_observation = [
        item
        for item in _source_queries(baseline_record)
        if item.get("resource_type") == "Observation"
    ]
    treatment_observation = [
        item
        for item in _source_queries(treatment_record)
        if item.get("resource_type") == "Observation"
    ]
    issues: list[str] = []
    if not baseline_observation:
        issues.append("baseline_observation_query_missing")
        return issues
    expected_width = len(baseline_observation)
    if len(treatment_observation) != len(MICRO_CODE_TEXT_TERMS) * expected_width:
        issues.append("four_query_union_cardinality")
        return issues

    baseline_signatures = [
        _observation_query_signature(item, expected_term=None)
        for item in baseline_observation
    ]
    if any(signature is None for signature in baseline_signatures):
        issues.append("baseline_observation_query_invalid")
        return issues

    for term_index, term in enumerate(MICRO_CODE_TEXT_TERMS):
        start = term_index * expected_width
        group = treatment_observation[start : start + expected_width]
        signatures = [
            _observation_query_signature(item, expected_term=term) for item in group
        ]
        if any(signature is None for signature in signatures):
            issues.append(f"term_or_query_invalid:{term}")
        elif signatures != baseline_signatures:
            issues.append(f"patient_date_sort_params_changed:{term}")
        if any(item.get("relaxation_policy") != "none" for item in group):
            issues.append(f"relaxation_policy_not_none:{term}")
        if any("relaxation_attempts" in item for item in group):
            issues.append(f"relaxation_attempt_recorded:{term}")
    return sorted(set(issues))


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _receipt_reference_matches(
    receipt: dict[str, Any], resources_by_id: dict[str, dict[str, Any]]
) -> bool:
    source = receipt.get("from")
    target = receipt.get("to")
    path = receipt.get("path")
    if not isinstance(source, str) or _FHIR_REFERENCE_RE.fullmatch(source) is None:
        return False
    target_match = (
        _FHIR_REFERENCE_RE.fullmatch(target) if isinstance(target, str) else None
    )
    path_match = _RECEIPT_PATH_RE.fullmatch(path) if isinstance(path, str) else None
    if target_match is None or path_match is None:
        return False
    source_type = source.split("/", 1)[0]
    if source_type != path_match.group("source"):
        return False
    target_type = _ALLOWED_RECEIPT_TARGETS.get((source_type, path_match.group("field")))
    if target_type != target_match.group("type"):
        return False

    resource = resources_by_id.get(source)
    if resource is None:
        return False
    raw_reference = resource.get(path_match.group("field"))
    raw_index = path_match.group("index")
    if isinstance(raw_reference, list):
        if raw_index is None:
            return False
        index = int(raw_index)
        if index >= len(raw_reference):
            return False
        raw_reference = raw_reference[index]
    elif raw_index is not None:
        return False
    return isinstance(raw_reference, dict) and raw_reference.get("reference") == target


def _traversal_integrity_for_question(
    v_record: dict[str, Any] | None,
    t_record: dict[str, Any] | None,
) -> dict[str, Any]:
    """Recompute every traversal invariant observable from frozen packets."""
    categories: dict[str, list[str]] = {
        "resource_shape": [],
        "actual_limits": [],
        "stats_consistency": [],
        "receipt_integrity": [],
    }
    traversal = _packet(t_record).get("reference_traversal")
    if not isinstance(traversal, dict):
        for issues in categories.values():
            issues.append("reference_traversal_missing")
        return {
            "passed": {name: False for name in categories},
            "issues": categories,
        }

    v_roots = _resources(v_record)
    t_resources = _resources(t_record)
    if t_resources[: len(v_roots)] != v_roots or len(t_resources) < len(v_roots):
        categories["resource_shape"].append("qt4v_root_prefix_changed")
    appended = t_resources[len(v_roots) :] if len(t_resources) >= len(v_roots) else []
    root_ids = [_resource_id(resource) for resource in v_roots]
    appended_ids = [_resource_id(resource) for resource in appended]
    all_ids = [_resource_id(resource) for resource in t_resources]
    valid_appended_ids = all(resource_id is not None for resource_id in appended_ids)
    if not valid_appended_ids:
        categories["resource_shape"].append("appended_resource_id_missing")
    if valid_appended_ids and appended_ids != sorted(appended_ids):
        categories["resource_shape"].append("appended_target_ids_not_sorted")
    present_ids = [resource_id for resource_id in all_ids if resource_id is not None]
    if len(present_ids) != len(set(present_ids)):
        categories["resource_shape"].append("resource_ids_not_deduplicated")
    if any(resource_id in set(root_ids) for resource_id in appended_ids):
        categories["resource_shape"].append("appended_target_duplicates_root")

    t_packet = _packet(t_record)
    if t_packet.get("resource_count") != len(t_resources):
        categories["resource_shape"].append("resource_count_mismatch")
    if t_packet.get("source_resource_ids") != sorted(present_ids):
        categories["resource_shape"].append("source_resource_id_ledger_mismatch")

    stats = traversal.get("stats")
    if not isinstance(stats, dict):
        stats = {}
        categories["stats_consistency"].append("stats_missing")
    receipts = traversal.get("path_receipts")
    if not isinstance(receipts, list) or not all(
        isinstance(receipt, dict) for receipt in receipts
    ):
        receipts = []
        categories["receipt_integrity"].append("path_receipts_invalid")

    actual_added_bytes = sum(_json_bytes(resource) for resource in appended)
    actual_receipt_bytes = _json_bytes(receipts)
    numeric_names = (
        "fetch_attempt_count",
        "added_resource_count",
        "added_serialized_bytes",
        "path_receipt_count",
        "path_receipt_serialized_bytes",
        "path_receipts_omitted",
    )
    numbers = {name: _nonnegative_int(stats.get(name)) for name in numeric_names}
    if any(value is None for value in numbers.values()):
        categories["stats_consistency"].append("nonnegative_integer_stat_required")

    if numbers["added_resource_count"] != len(appended):
        categories["resource_shape"].append("added_resource_count_mismatch")
    if numbers["added_serialized_bytes"] != actual_added_bytes:
        categories["resource_shape"].append("added_serialized_bytes_mismatch")
    if numbers["path_receipt_count"] != len(receipts):
        categories["stats_consistency"].append("path_receipt_count_mismatch")
    if numbers["path_receipt_serialized_bytes"] != actual_receipt_bytes:
        categories["stats_consistency"].append("path_receipt_serialized_bytes_mismatch")

    status_counts = stats.get("path_status_counts")
    valid_status_counts = isinstance(status_counts, dict) and set(status_counts) == set(
        PATH_STATUSES
    )
    if valid_status_counts:
        normalized_status_counts = {
            status: _nonnegative_int(status_counts.get(status))
            for status in PATH_STATUSES
        }
        valid_status_counts = all(
            value is not None for value in normalized_status_counts.values()
        )
    else:
        normalized_status_counts = {status: None for status in PATH_STATUSES}
    if not valid_status_counts:
        categories["stats_consistency"].append("path_status_counts_invalid")

    serialized_status_counts = Counter(
        receipt.get("status")
        for receipt in receipts
        if receipt.get("status") in PATH_STATUSES
    )
    if valid_status_counts:
        total_status_count = sum(
            int(normalized_status_counts[status]) for status in PATH_STATUSES
        )
        omitted = numbers["path_receipts_omitted"]
        if omitted is None or total_status_count != len(receipts) + omitted:
            categories["stats_consistency"].append("status_total_omission_mismatch")
        if any(
            serialized_status_counts[status] > int(normalized_status_counts[status])
            for status in PATH_STATUSES
        ):
            categories["stats_consistency"].append(
                "serialized_status_exceeds_complete_count"
            )
        if normalized_status_counts["fetched"] != len(appended):
            categories["stats_consistency"].append(
                "fetched_status_added_resource_mismatch"
            )
        if omitted == 0 and any(
            serialized_status_counts[status] != normalized_status_counts[status]
            for status in PATH_STATUSES
        ):
            categories["stats_consistency"].append("complete_status_count_mismatch")

    attempts = numbers["fetch_attempt_count"]
    observed_attempt_targets = {
        str(receipt.get("to"))
        for receipt in receipts
        if receipt.get("status") in _FETCH_ATTEMPT_STATUSES
    }
    if attempts is not None:
        if attempts < len(observed_attempt_targets) or attempts < len(appended):
            categories["stats_consistency"].append("fetch_attempt_count_too_small")
        if numbers["path_receipts_omitted"] == 0 and attempts != len(
            observed_attempt_targets
        ):
            categories["stats_consistency"].append("fetch_attempt_count_mismatch")

    max_depth_observed = 0
    resources_by_id = {
        resource_id: resource
        for resource in t_resources
        if (resource_id := _resource_id(resource)) is not None
    }
    appended_id_set = {
        resource_id for resource_id in appended_ids if resource_id is not None
    }
    all_id_set = set(resources_by_id)
    expected_receipt_keys = {"depth", "from", "path", "to", "status"}
    for receipt in receipts:
        depth = _nonnegative_int(receipt.get("depth"))
        if depth is None or depth < 1:
            categories["receipt_integrity"].append("receipt_depth_invalid")
        else:
            max_depth_observed = max(max_depth_observed, depth)
        if set(receipt) != expected_receipt_keys:
            categories["receipt_integrity"].append("receipt_shape_invalid")
        status = receipt.get("status")
        if status not in PATH_STATUSES:
            categories["receipt_integrity"].append("receipt_status_invalid")
        if not _receipt_reference_matches(receipt, resources_by_id):
            categories["receipt_integrity"].append(
                "receipt_path_or_reference_not_allowed"
            )
        target = receipt.get("to")
        if status == "fetched" and target not in appended_id_set:
            categories["receipt_integrity"].append("fetched_target_not_appended")
        if status == "already_present" and target not in all_id_set:
            categories["receipt_integrity"].append("already_present_target_absent")
        if (
            status in {"missing", "max_resources", "max_serialized_bytes"}
            and target in all_id_set
        ):
            categories["receipt_integrity"].append("unfetched_target_present")

    if attempts is None or attempts > FROZEN_TRAVERSAL_LIMITS["max_resources"]:
        categories["actual_limits"].append("fetch_attempt_limit_exceeded")
    if len(appended) > FROZEN_TRAVERSAL_LIMITS["max_resources"]:
        categories["actual_limits"].append("added_resource_limit_exceeded")
    if actual_added_bytes > FROZEN_TRAVERSAL_LIMITS["max_serialized_bytes"]:
        categories["actual_limits"].append("added_serialized_byte_limit_exceeded")
    if len(receipts) > FROZEN_TRAVERSAL_LIMITS["max_path_receipts"]:
        categories["actual_limits"].append("receipt_count_limit_exceeded")
    if actual_receipt_bytes > FROZEN_TRAVERSAL_LIMITS["max_path_receipt_bytes"]:
        categories["actual_limits"].append("receipt_byte_limit_exceeded")
    if max_depth_observed > FROZEN_TRAVERSAL_LIMITS["max_depth"]:
        categories["actual_limits"].append("receipt_depth_limit_exceeded")

    categories = {name: sorted(set(issues)) for name, issues in categories.items()}
    return {
        "passed": {name: not issues for name, issues in categories.items()},
        "issues": categories,
        "observed": {
            "appended_resource_count": len(appended),
            "appended_serialized_bytes": actual_added_bytes,
            "serialized_receipt_count": len(receipts),
            "serialized_receipt_bytes": actual_receipt_bytes,
            "max_serialized_depth": max_depth_observed,
        },
    }


def _traversal_metrics(
    records: dict[str, dict[str, Any]], question_ids: Iterable[str]
) -> dict[str, Any]:
    status_counts: Counter[str] = Counter({status: 0 for status in PATH_STATUSES})
    family_counts: Counter[str] = Counter({family: 0 for family in PATH_FAMILIES})
    depth_counts: Counter[str] = Counter()
    traversal_packets = 0
    questions_with_fetched_target = 0
    fetch_attempt_count = 0
    added_resource_count = 0
    added_serialized_bytes = 0
    serialized_receipts = 0
    omitted_receipts = 0
    configured_depths: set[int] = set()

    for question_id in sorted(question_ids):
        traversal = _packet(records.get(question_id)).get("reference_traversal")
        if not isinstance(traversal, dict):
            continue
        traversal_packets += 1
        stats = (
            traversal.get("stats") if isinstance(traversal.get("stats"), dict) else {}
        )
        raw_statuses = (
            stats.get("path_status_counts")
            if isinstance(stats.get("path_status_counts"), dict)
            else {}
        )
        for status, count in raw_statuses.items():
            normalized_count = _nonnegative_int(count)
            if normalized_count is not None:
                status_counts[str(status)] += normalized_count
        fetched_count = _nonnegative_int(raw_statuses.get("fetched")) or 0
        questions_with_fetched_target += int(fetched_count > 0)
        fetch_attempt_count += _nonnegative_int(stats.get("fetch_attempt_count")) or 0
        added_resource_count += _nonnegative_int(stats.get("added_resource_count")) or 0
        added_serialized_bytes += (
            _nonnegative_int(stats.get("added_serialized_bytes")) or 0
        )
        omitted_receipts += _nonnegative_int(stats.get("path_receipts_omitted")) or 0
        limits = (
            traversal.get("limits") if isinstance(traversal.get("limits"), dict) else {}
        )
        if isinstance(limits.get("max_depth"), int):
            configured_depths.add(limits["max_depth"])

        receipts = traversal.get("path_receipts")
        if not isinstance(receipts, list):
            continue
        for receipt in receipts:
            if not isinstance(receipt, dict):
                continue
            serialized_receipts += 1
            family_counts[_path_family(receipt.get("path"))] += 1
            try:
                depth_counts[str(int(receipt.get("depth")))] += 1
            except (TypeError, ValueError):
                pass

    targets = {
        "fetched": status_counts["fetched"],
        "already_present": status_counts["already_present"],
        "missing": status_counts["missing"],
        "resource_capped": status_counts["max_resources"],
        "byte_capped": status_counts["max_serialized_bytes"],
    }
    return {
        "questions_with_traversal_receipts": traversal_packets,
        "questions_with_fetched_target": questions_with_fetched_target,
        "fetch_attempt_count": fetch_attempt_count,
        "added_resource_count": added_resource_count,
        "added_serialized_bytes": added_serialized_bytes,
        "target_outcomes": targets,
        "raw_path_status_counts": dict(sorted(status_counts.items())),
        "serialized_path_receipt_count": serialized_receipts,
        "path_receipts_omitted": omitted_receipts,
        "serialized_path_depth_counts": dict(
            sorted(depth_counts.items(), key=lambda item: int(item[0]))
        ),
        "max_serialized_depth_observed": max(
            (int(depth) for depth in depth_counts), default=0
        ),
        "configured_max_depths": sorted(configured_depths),
        "serialized_path_family_counts": dict(sorted(family_counts.items())),
        "diagnostic_report_path_use": {
            "DiagnosticReport.result": family_counts["DiagnosticReport.result"],
            "DiagnosticReport.specimen": family_counts["DiagnosticReport.specimen"],
            "total": family_counts["DiagnosticReport.result"]
            + family_counts["DiagnosticReport.specimen"],
        },
        "path_count_scope": (
            "Path-family and depth counts cover serialized path receipts only; "
            "target outcome counts come from complete pre-truncation stats."
        ),
    }


def _footprint_for_arm(
    arm: str,
    records: dict[str, dict[str, Any]],
    scheduled_ids: Iterable[str],
    qt4v_records: dict[str, dict[str, Any]],
) -> dict[str, int]:
    totals = {
        "packet_count": 0,
        "root_resource_count": 0,
        "root_resource_json_bytes": 0,
        "resource_count": 0,
        "resource_json_bytes": 0,
        "packet_json_bytes": 0,
    }
    for question_id in scheduled_ids:
        record = records.get(question_id)
        if record is None:
            continue
        resources = _resources(record)
        if arm == "qt4t":
            root_count = len(_resources(qt4v_records.get(question_id)))
            roots = resources[:root_count]
        else:
            roots = resources
        totals["packet_count"] += 1
        totals["root_resource_count"] += len(roots)
        totals["root_resource_json_bytes"] += _json_bytes(roots)
        totals["resource_count"] += len(resources)
        totals["resource_json_bytes"] += _json_bytes(resources)
        totals["packet_json_bytes"] += _json_bytes(_packet(record))
    return totals


def _numeric_delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    return {
        key: after[key] - before[key]
        for key in sorted(before)
        if key != "packet_count" and key in after
    }


def _gate(
    gates: list[dict[str, Any]],
    name: str,
    passed: bool,
    *,
    observed: Any,
    expected: Any,
) -> None:
    gates.append(
        {
            "name": name,
            "passed": bool(passed),
            "observed": observed,
            "expected": expected,
        }
    )


def compare_packet_arms(
    *,
    arms: dict[str, dict[str, dict[str, Any]]],
    spec: QuestionSpec,
    expectations: GateExpectations = GateExpectations(),
    input_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare three already-loaded arms and return a deterministic report."""
    missing_arm_names = set(ARM_NAMES) - set(arms)
    if missing_arm_names:
        raise GateInputError(f"missing arms: {sorted(missing_arm_names)}")

    scheduled_ids = list(spec.scheduled_ids)
    scheduled_set = set(scheduled_ids)
    micro_ids = set(spec.microbiology_ids)
    non_micro_ids = scheduled_set - micro_ids
    gates: list[dict[str, Any]] = []

    question_set_observed: dict[str, Any] = {}
    question_sets_match = True
    for arm in ARM_NAMES:
        actual = set(arms[arm])
        missing = sorted(scheduled_set - actual)
        extra = sorted(actual - scheduled_set)
        question_set_observed[arm] = {
            "count": len(actual),
            "missing": missing,
            "extra": extra,
        }
        question_sets_match = question_sets_match and not missing and not extra
    _gate(
        gates,
        "scheduled_question_sets",
        question_sets_match,
        observed=question_set_observed,
        expected={"count": len(scheduled_ids), "missing": [], "extra": []},
    )

    expected_count_checks = (
        ("expected_total_questions", len(scheduled_ids), expectations.expected_total),
        (
            "expected_microbiology_questions",
            len(micro_ids),
            expectations.expected_micro,
        ),
        (
            "expected_non_microbiology_questions",
            len(non_micro_ids),
            expectations.expected_non_micro,
        ),
    )
    for name, observed, expected in expected_count_checks:
        if expected is not None:
            _gate(
                gates, name, observed == expected, observed=observed, expected=expected
            )

    prompt_metadata_failures: dict[str, dict[str, list[str]]] = {}
    for arm in ARM_NAMES:
        for question_id in scheduled_ids:
            input_row = spec.input_rows.get(question_id)
            if input_row is None:
                issues = ["frozen_input_row_missing"]
            else:
                issues = _prompt_metadata_issues(
                    input_row, arms[arm].get(question_id)
                )
            if issues:
                prompt_metadata_failures.setdefault(arm, {})[question_id] = issues
    prompt_metadata_total = len(ARM_NAMES) * len(scheduled_ids)
    prompt_metadata_matched = prompt_metadata_total - sum(
        len(failures) for failures in prompt_metadata_failures.values()
    )
    _gate(
        gates,
        "effective_prompt_metadata_matches_frozen_input",
        not prompt_metadata_failures,
        observed={
            "matched": prompt_metadata_matched,
            "total": prompt_metadata_total,
            "failures": prompt_metadata_failures,
        },
        expected={
            "matched": prompt_metadata_total,
            "total": prompt_metadata_total,
            "failures": {},
        },
    )

    live_observed = {
        arm: sum(
            1
            for question_id in scheduled_ids
            if arms[arm].get(question_id)
            and not bool(_packet(arms[arm][question_id]).get("plan_only"))
        )
        for arm in ARM_NAMES
    }
    _gate(
        gates,
        "live_packet_count",
        all(count == len(scheduled_ids) for count in live_observed.values()),
        observed=live_observed,
        expected={arm: len(scheduled_ids) for arm in ARM_NAMES},
    )

    question_text_failures: dict[str, dict[str, str | None]] = {}
    recomputed_micro_ids: set[str] = set()
    for question_id in scheduled_ids:
        input_row = spec.input_rows.get(question_id, {})
        arm_questions = {
            arm: _question_text(arms[arm].get(question_id), input_row)
            for arm in ARM_NAMES
        }
        if (
            any(question is None for question in arm_questions.values())
            or len(set(arm_questions.values())) != 1
        ):
            question_text_failures[question_id] = arm_questions
            continue
        if _micro_dispatches(arm_questions["a6a"]):
            recomputed_micro_ids.add(question_id)
    _gate(
        gates,
        "micro_dispatch_v1_question_text_consistency",
        not question_text_failures,
        observed={
            "matched": len(scheduled_ids) - len(question_text_failures),
            "failures": question_text_failures,
        },
        expected={"matched": len(scheduled_ids), "failures": {}},
    )
    dispatch_false_positive_ids = sorted(recomputed_micro_ids - micro_ids)
    dispatch_false_negative_ids = sorted(micro_ids - recomputed_micro_ids)
    _gate(
        gates,
        "micro_dispatch_v1_matches_analysis_stratum",
        not dispatch_false_positive_ids and not dispatch_false_negative_ids,
        observed={
            "dispatched": len(recomputed_micro_ids),
            "analysis_microbiology_matched": len(recomputed_micro_ids & micro_ids),
            "analysis_non_microbiology_dispatched": len(
                recomputed_micro_ids - micro_ids
            ),
            "false_positive_ids": dispatch_false_positive_ids,
            "false_negative_ids": dispatch_false_negative_ids,
        },
        expected={
            "dispatched": len(micro_ids),
            "analysis_microbiology_matched": len(micro_ids),
            "analysis_non_microbiology_dispatched": 0,
            "false_positive_ids": [],
            "false_negative_ids": [],
        },
    )

    dispatch_sets = {
        arm: {
            question_id
            for question_id in scheduled_ids
            if _features(arms[arm].get(question_id))
        }
        for arm in ARM_NAMES
    }
    dispatch_shapes = {
        "a6a": all(
            _features(arms["a6a"].get(question_id)) == ()
            for question_id in scheduled_ids
        ),
        "qt4v": all(
            _features(arms["qt4v"].get(question_id))
            == (V_FEATURES if question_id in micro_ids else ())
            for question_id in scheduled_ids
        ),
        "qt4t": all(
            _features(arms["qt4t"].get(question_id))
            == (T_FEATURES if question_id in micro_ids else ())
            for question_id in scheduled_ids
        ),
    }
    _gate(
        gates,
        "a6a_dispatch_none",
        dispatch_shapes["a6a"],
        observed=sorted(dispatch_sets["a6a"]),
        expected=[],
    )
    for arm in ("qt4v", "qt4t"):
        _gate(
            gates,
            f"{arm}_dispatch_exact",
            dispatch_shapes[arm] and dispatch_sets[arm] == micro_ids,
            observed=sorted(dispatch_sets[arm]),
            expected=sorted(micro_ids),
        )

    recomputed_dispatch_shapes = {
        "a6a": all(
            _features(arms["a6a"].get(question_id)) == ()
            for question_id in scheduled_ids
        ),
        "qt4v": all(
            _features(arms["qt4v"].get(question_id))
            == (V_FEATURES if question_id in recomputed_micro_ids else ())
            for question_id in scheduled_ids
        ),
        "qt4t": all(
            _features(arms["qt4t"].get(question_id))
            == (T_FEATURES if question_id in recomputed_micro_ids else ())
            for question_id in scheduled_ids
        ),
    }
    _gate(
        gates,
        "micro_dispatch_v1_feature_application",
        all(recomputed_dispatch_shapes.values()),
        observed=recomputed_dispatch_shapes,
        expected={arm: True for arm in ARM_NAMES},
    )

    forbidden_paths: dict[str, dict[str, list[str]]] = {}
    for arm in ARM_NAMES:
        for question_id in scheduled_ids:
            paths = _forbidden_packet_paths(_packet(arms[arm].get(question_id)))
            if paths:
                forbidden_paths.setdefault(arm, {})[question_id] = paths
    _gate(
        gates,
        "packets_exclude_benchmark_answer_keys",
        not forbidden_paths,
        observed=forbidden_paths,
        expected={},
    )

    query_fetch_failures: dict[str, dict[str, list[str]]] = {}
    for arm in ARM_NAMES:
        # Only the frozen microbiology stratum is answer-bearing in this
        # screen.  Its 42 rows were rebuilt with fail-closed query receipts;
        # the 367 legacy negative controls are used solely for literal
        # packet/prompt no-op checks and never reach an answering model.
        for question_id in sorted(micro_ids):
            issues = _query_fetch_issues(arms[arm].get(question_id))
            if issues:
                query_fetch_failures.setdefault(arm, {})[question_id] = issues
    query_fetch_total = len(ARM_NAMES) * len(micro_ids)
    query_fetch_matched = query_fetch_total - sum(
        len(items) for items in query_fetch_failures.values()
    )
    _gate(
        gates,
        "query_fetch_receipts_complete_and_error_free",
        not query_fetch_failures,
        observed={
            "matched": query_fetch_matched,
            "total": query_fetch_total,
            "failures": query_fetch_failures,
        },
        expected={
            "matched": query_fetch_total,
            "total": query_fetch_total,
            "failures": {},
        },
    )

    non_micro_packet_matches = 0
    non_micro_prompt_matches = 0
    for question_id in sorted(non_micro_ids):
        records = [arms[arm].get(question_id) for arm in ARM_NAMES]
        if not all(records):
            continue
        input_row = spec.input_rows.get(question_id, {})
        packets = [_canonical_json(_packet(record)) for record in records if record]
        try:
            prompts = [_prompt(input_row, record) for record in records if record]
        except (TypeError, ValueError):
            prompts = []
        non_micro_packet_matches += int(len(set(packets)) == 1)
        non_micro_prompt_matches += int(
            len(prompts) == len(ARM_NAMES) and len(set(prompts)) == 1
        )
    _gate(
        gates,
        "non_micro_packet_equivalence",
        non_micro_packet_matches == len(non_micro_ids),
        observed=non_micro_packet_matches,
        expected=len(non_micro_ids),
    )
    _gate(
        gates,
        "non_micro_prompt_equivalence",
        non_micro_prompt_matches == len(non_micro_ids),
        observed=non_micro_prompt_matches,
        expected=len(non_micro_ids),
    )

    micro_plan_matches = 0
    micro_root_matches = 0
    for question_id in sorted(micro_ids):
        v_record = arms["qt4v"].get(question_id)
        t_record = arms["qt4t"].get(question_id)
        if not v_record or not t_record:
            continue
        v_packet = _packet(v_record)
        t_packet = _packet(t_record)
        micro_plan_matches += int(
            v_packet.get("source_queries") == t_packet.get("source_queries")
        )
        v_roots = _resources(v_record)
        t_resources = _resources(t_record)
        roots_match = t_resources[: len(v_roots)] == v_roots and v_packet.get(
            "bounds"
        ) == t_packet.get("bounds")
        micro_root_matches += int(roots_match)
    _gate(
        gates,
        "qt4v_qt4t_micro_search_plan_equivalence",
        micro_plan_matches == len(micro_ids),
        observed=micro_plan_matches,
        expected=len(micro_ids),
    )
    _gate(
        gates,
        "qt4v_qt4t_micro_root_equivalence",
        micro_root_matches == len(micro_ids),
        observed=micro_root_matches,
        expected=len(micro_ids),
    )

    micro_query_audit: dict[str, dict[str, list[str]]] = {"qt4v": {}, "qt4t": {}}
    for arm in ("qt4v", "qt4t"):
        for question_id in sorted(micro_ids):
            issues = _micro_vocab_query_issues(
                arms["a6a"].get(question_id), arms[arm].get(question_id)
            )
            if issues:
                micro_query_audit[arm][question_id] = issues
        _gate(
            gates,
            f"{arm}_micro_v1_observation_query_union",
            not micro_query_audit[arm],
            observed={
                "matched": len(micro_ids) - len(micro_query_audit[arm]),
                "failures": micro_query_audit[arm],
            },
            expected={"matched": len(micro_ids), "failures": {}},
        )

    all_ids = scheduled_ids
    recall = {
        "overall": {
            arm: _recall_metrics(arms[arm], spec, all_ids) for arm in ARM_NAMES
        },
        "microbiology": {
            arm: _recall_metrics(arms[arm], spec, sorted(micro_ids))
            for arm in ARM_NAMES
        },
    }
    vocab_change = _gold_change(arms["a6a"], arms["qt4v"], spec, sorted(micro_ids))
    traversal_change = _gold_change(arms["qt4v"], arms["qt4t"], spec, sorted(micro_ids))
    traversal = _traversal_metrics(arms["qt4t"], sorted(micro_ids))
    traversal_contract_matches = 0
    traversal_contract_variants: Counter[str] = Counter()
    for question_id in sorted(micro_ids):
        candidate = _packet(arms["qt4t"].get(question_id)).get("reference_traversal")
        if isinstance(candidate, dict):
            observed_contract = {
                "kind": candidate.get("kind"),
                "version": candidate.get("version"),
                "limits": candidate.get("limits"),
            }
        else:
            observed_contract = {"kind": None, "version": None, "limits": None}
        traversal_contract_variants[_canonical_json(observed_contract)] += 1
        traversal_contract_matches += int(
            observed_contract
            == {
                "kind": FROZEN_TRAVERSAL_KIND,
                "version": FROZEN_TRAVERSAL_VERSION,
                "limits": FROZEN_TRAVERSAL_LIMITS,
            }
        )
    traversal["frozen_contract"] = {
        "matched": traversal_contract_matches,
        "total": len(micro_ids),
        "expected": {
            "kind": FROZEN_TRAVERSAL_KIND,
            "version": FROZEN_TRAVERSAL_VERSION,
            "limits": FROZEN_TRAVERSAL_LIMITS,
        },
        "observed_variants": [
            {"contract": json.loads(contract), "count": count}
            for contract, count in sorted(traversal_contract_variants.items())
        ],
    }
    traversal_integrity_by_question = {
        question_id: _traversal_integrity_for_question(
            arms["qt4v"].get(question_id), arms["qt4t"].get(question_id)
        )
        for question_id in sorted(micro_ids)
    }
    integrity_category_gate_names = {
        "resource_shape": "qt4t_traversal_resource_shape",
        "actual_limits": "qt4t_traversal_actual_limits",
        "stats_consistency": "qt4t_traversal_stats_consistency",
        "receipt_integrity": "qt4t_traversal_receipt_integrity",
    }
    integrity_summary: dict[str, Any] = {}
    for category, gate_name in integrity_category_gate_names.items():
        failures = {
            question_id: result["issues"][category]
            for question_id, result in traversal_integrity_by_question.items()
            if not result["passed"][category]
        }
        integrity_summary[category] = {
            "matched": len(micro_ids) - len(failures),
            "total": len(micro_ids),
            "failures": failures,
        }
        _gate(
            gates,
            gate_name,
            not failures,
            observed=integrity_summary[category],
            expected={
                "matched": len(micro_ids),
                "total": len(micro_ids),
                "failures": {},
            },
        )
    traversal["integrity"] = {
        "categories": integrity_summary,
        "per_question_observed": {
            question_id: result.get("observed", {})
            for question_id, result in traversal_integrity_by_question.items()
        },
        "visibility_limit": (
            "When path_receipts_omitted is nonzero, serialized receipts cannot "
            "independently identify every attempted target. The gate still hard-checks "
            "complete-count arithmetic, serialized-receipt lower bounds, appended "
            "resource counts/bytes, and every frozen limit observable in the packet."
        ),
    }
    _gate(
        gates,
        "qt4v_minimum_vocab_gold_gain",
        vocab_change["gold_id_occurrences_gained"] >= expectations.min_vocab_gold_gain,
        observed=vocab_change["gold_id_occurrences_gained"],
        expected={"minimum": expectations.min_vocab_gold_gain},
    )
    _gate(
        gates,
        "qt4t_traversal_fetch_observed",
        traversal["target_outcomes"]["fetched"] > 0,
        observed=traversal["target_outcomes"]["fetched"],
        expected={"minimum": 1},
    )
    _gate(
        gates,
        "qt4t_frozen_traversal_contract",
        traversal_contract_matches == len(micro_ids),
        observed=traversal_contract_matches,
        expected=len(micro_ids),
    )
    _gate(
        gates,
        "qt4t_minimum_traversal_gold_gain",
        traversal_change["gold_id_occurrences_gained"]
        >= expectations.min_traversal_gold_gain,
        observed=traversal_change["gold_id_occurrences_gained"],
        expected={"minimum": expectations.min_traversal_gold_gain},
    )

    footprint_arms = {
        arm: _footprint_for_arm(arm, arms[arm], scheduled_ids, arms["qt4v"])
        for arm in ARM_NAMES
    }
    resource_footprint = {
        "byte_definition": (
            "UTF-8 bytes of deterministic compact JSON; QT-4T roots are the "
            "validated QT-4V resource prefix."
        ),
        "arms": footprint_arms,
        "deltas": {
            "qt4v_minus_a6a": _numeric_delta(
                footprint_arms["qt4v"], footprint_arms["a6a"]
            ),
            "qt4t_minus_qt4v": _numeric_delta(
                footprint_arms["qt4t"], footprint_arms["qt4v"]
            ),
        },
    }

    failed_gates = [gate["name"] for gate in gates if not gate["passed"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "passed": not failed_gates,
        "scheduled_question_count": len(scheduled_ids),
        "scheduled_question_ids": scheduled_ids,
        "inputs": input_metadata or {},
        "dispatch": {
            "version": MICRO_DISPATCH_VERSION,
            "question_terms": list(MICRO_DISPATCH_TERMS),
            "microbiology_questions": len(micro_ids),
            "non_microbiology_questions": len(non_micro_ids),
            "microbiology_question_ids": sorted(micro_ids),
            "recomputed_microbiology_question_ids": sorted(recomputed_micro_ids),
            "analysis_microbiology_matched": len(recomputed_micro_ids & micro_ids),
            "analysis_non_microbiology_dispatched": len(
                recomputed_micro_ids - micro_ids
            ),
            "a6a_dispatched": len(dispatch_sets["a6a"]),
            "qt4v_dispatched": len(dispatch_sets["qt4v"]),
            "qt4t_dispatched": len(dispatch_sets["qt4t"]),
        },
        "equivalence": {
            "effective_prompt_metadata": {
                "matched": prompt_metadata_matched,
                "total": prompt_metadata_total,
                "failures": prompt_metadata_failures,
                "bound_fields": list(PROMPT_METADATA_FIELDS),
                "overlay": "{**frozen_input_row, **packet_record}",
            },
            "non_micro_packet": {
                "matched": non_micro_packet_matches,
                "total": len(non_micro_ids),
            },
            "non_micro_prompt": {
                "matched": non_micro_prompt_matches,
                "total": len(non_micro_ids),
            },
            "micro_qt4v_qt4t_search_plan": {
                "matched": micro_plan_matches,
                "total": len(micro_ids),
            },
            "micro_qt4v_qt4t_roots": {
                "matched": micro_root_matches,
                "total": len(micro_ids),
            },
            "micro_v1_query_union": {
                arm: {
                    "version": MICRO_VOCABULARY_VERSION,
                    "terms": list(MICRO_CODE_TEXT_TERMS),
                    "matched": len(micro_ids) - len(micro_query_audit[arm]),
                    "total": len(micro_ids),
                    "failures": micro_query_audit[arm],
                }
                for arm in ("qt4v", "qt4t")
            },
        },
        "query_fetch_audit": {
            "supported": True,
            "hard_gate_applied": True,
            "scope": "answer-bearing microbiology stratum only",
            "negative_controls": (
                "mechanical packet/prompt no-op checks; no model answers"
            ),
            "matched": query_fetch_matched,
            "total": query_fetch_total,
            "failures": query_fetch_failures,
        },
        "resource_footprint": resource_footprint,
        "traversal": traversal,
        "evaluation_only_gold_metrics": {
            "warning": (
                "EVALUATION ONLY: true_fhir_ids are used only after packet construction "
                "to measure retrieval; never expose this section to an answering model."
            ),
            "recall": recall,
            "vocabulary_gold_change": vocab_change,
            "traversal_gold_gain": traversal_change,
        },
        "gate_expectations": {
            "expected_total": expectations.expected_total,
            "expected_micro": expectations.expected_micro,
            "expected_non_micro": expectations.expected_non_micro,
            "min_vocab_gold_gain": expectations.min_vocab_gold_gain,
            "min_traversal_gold_gain": expectations.min_traversal_gold_gain,
        },
        "gates": gates,
        "failed_gates": failed_gates,
    }


def compare_packet_files(
    *,
    a6a_path: Path,
    qt4v_path: Path,
    qt4t_path: Path,
    question_spec_path: Path,
    expectations: GateExpectations = GateExpectations(),
) -> dict[str, Any]:
    paths = {
        "a6a": a6a_path,
        "qt4v": qt4v_path,
        "qt4t": qt4t_path,
        "question_spec": question_spec_path,
    }
    arms = {arm: load_packet_records(paths[arm]) for arm in ARM_NAMES}
    spec = load_question_spec(question_spec_path)
    input_metadata = {
        name: {"path": str(path), "sha256": _sha256_file(path)}
        for name, path in sorted(paths.items())
    }
    return compare_packet_arms(
        arms=arms,
        spec=spec,
        expectations=expectations,
        input_metadata=input_metadata,
    )


def render_json(report: dict[str, Any]) -> str:
    """Render stable machine-readable output without timestamps."""
    return json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.1%}"


def render_text(report: dict[str, Any]) -> str:
    """Render a concise human-readable companion to the JSON gate."""
    status = "PASS" if report["passed"] else "FAIL"
    dispatch = report["dispatch"]
    equivalence = report["equivalence"]
    traversal = report["traversal"]
    gold = report["evaluation_only_gold_metrics"]
    lines = [
        f"QT-4 zero-model packet gate: {status}",
        (
            f"scheduled: {report['scheduled_question_count']} "
            f"(microbiology={dispatch['microbiology_questions']}, "
            f"non-microbiology={dispatch['non_microbiology_questions']})"
        ),
        (
            "frozen prompt metadata: "
            f"{equivalence['effective_prompt_metadata']['matched']}/"
            f"{equivalence['effective_prompt_metadata']['total']}"
        ),
        (
            "non-micro packet no-op: "
            f"{equivalence['non_micro_packet']['matched']}/"
            f"{equivalence['non_micro_packet']['total']}"
        ),
        (
            "non-micro prompt no-op: "
            f"{equivalence['non_micro_prompt']['matched']}/"
            f"{equivalence['non_micro_prompt']['total']}"
        ),
        "EVALUATION-ONLY gold metrics (never model-visible):",
    ]
    for stratum in ("overall", "microbiology"):
        recall = gold["recall"][stratum]
        lines.append(
            f"  {stratum} weighted recall: "
            + ", ".join(
                f"{arm}={_pct(recall[arm]['id_weighted_recall'])}" for arm in ARM_NAMES
            )
        )
        lines.append(
            f"  {stratum} macro recall: "
            + ", ".join(
                f"{arm}={_pct(recall[arm]['macro_recall'])}" for arm in ARM_NAMES
            )
        )
    lines.extend(
        [
            (
                "  vocabulary gold IDs gained/lost: "
                f"{gold['vocabulary_gold_change']['gold_id_occurrences_gained']}/"
                f"{gold['vocabulary_gold_change']['gold_id_occurrences_lost']}"
            ),
            (
                "  traversal gold IDs gained/lost: "
                f"{gold['traversal_gold_gain']['gold_id_occurrences_gained']}/"
                f"{gold['traversal_gold_gain']['gold_id_occurrences_lost']}"
            ),
            (
                "traversal target outcomes: "
                + ", ".join(
                    f"{name}={count}"
                    for name, count in traversal["target_outcomes"].items()
                )
            ),
            (
                "DiagnosticReport path use (serialized receipts): "
                f"DiagnosticReport.result="
                f"{traversal['diagnostic_report_path_use']['DiagnosticReport.result']}, "
                f"DiagnosticReport.specimen="
                f"{traversal['diagnostic_report_path_use']['DiagnosticReport.specimen']}"
            ),
        ]
    )
    query_fetch = report.get("query_fetch_audit", {})
    if query_fetch.get("supported"):
        lines.append(
            "query fetch receipt/error audit: "
            f"{query_fetch.get('matched', 0)}/{query_fetch.get('total', 0)} passed"
        )
    else:
        lines.append(
            "query fetch receipt/error audit: unavailable in current packet format "
            "(no hard gate invented)"
        )
    if report["failed_gates"]:
        lines.append("failed gates: " + ", ".join(report["failed_gates"]))
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare frozen A6a/QT-4V/QT-4T packets without model calls."
    )
    parser.add_argument("--a6a", type=Path, required=True)
    parser.add_argument("--qt4v", type=Path, required=True)
    parser.add_argument("--qt4t", type=Path, required=True)
    parser.add_argument("--question-spec", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--text-out", type=Path, required=True)
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--expected-micro", type=int)
    parser.add_argument("--expected-non-micro", type=int)
    parser.add_argument("--min-vocab-gold-gain", type=int, default=1)
    parser.add_argument("--min-traversal-gold-gain", type=int, default=1)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = compare_packet_files(
            a6a_path=args.a6a,
            qt4v_path=args.qt4v,
            qt4t_path=args.qt4t,
            question_spec_path=args.question_spec,
            expectations=GateExpectations(
                expected_total=args.expected_total,
                expected_micro=args.expected_micro,
                expected_non_micro=args.expected_non_micro,
                min_vocab_gold_gain=args.min_vocab_gold_gain,
                min_traversal_gold_gain=args.min_traversal_gold_gain,
            ),
        )
    except (GateInputError, OSError) as exc:
        print(f"qt4 packet gate input error: {exc}", file=sys.stderr)
        return 2

    json_text = render_json(report)
    text = render_text(report)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.text_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json_text, encoding="utf-8")
    args.text_out.write_text(text, encoding="utf-8")
    if not args.quiet:
        print(text, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
