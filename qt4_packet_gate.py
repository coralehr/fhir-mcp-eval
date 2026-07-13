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
FROZEN_TRAVERSAL_LIMITS = {
    "max_depth": 2,
    "max_resources": 24,
    "max_serialized_bytes": 24_000,
    "max_path_receipts": 48,
    "max_path_receipt_bytes": 12_000,
}


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
    for row in rows:
        question_id = _question_id(row.get("question_id"), source=source)
        if question_id in seen:
            raise GateInputError(f"duplicate question_id {question_id!r} in {source}")
        seen.add(question_id)
        scheduled.append(question_id)
        if _row_is_microbiology(row):
            microbiology.add(question_id)
        gold[question_id] = _parse_gold_ids(row.get("true_fhir_ids"))
    if not scheduled:
        raise GateInputError(f"question specification has no rows: {source}")
    return QuestionSpec(
        scheduled_ids=tuple(sorted(scheduled)),
        microbiology_ids=frozenset(microbiology),
        gold_ids=gold,
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


def _prompt(record: dict[str, Any]) -> str:
    return build_prompt(record, mode="packet")


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
            try:
                status_counts[str(status)] += int(count)
            except (TypeError, ValueError):
                continue
        questions_with_fetched_target += int(int(raw_statuses.get("fetched") or 0) > 0)
        fetch_attempt_count += int(stats.get("fetch_attempt_count") or 0)
        added_resource_count += int(stats.get("added_resource_count") or 0)
        added_serialized_bytes += int(stats.get("added_serialized_bytes") or 0)
        omitted_receipts += int(stats.get("path_receipts_omitted") or 0)
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

    non_micro_packet_matches = 0
    non_micro_prompt_matches = 0
    for question_id in sorted(non_micro_ids):
        records = [arms[arm].get(question_id) for arm in ARM_NAMES]
        if not all(records):
            continue
        packets = [_canonical_json(_packet(record)) for record in records if record]
        prompts = [_prompt(record) for record in records if record]
        non_micro_packet_matches += int(len(set(packets)) == 1)
        non_micro_prompt_matches += int(len(set(prompts)) == 1)
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
                "version": candidate.get("version"),
                "limits": candidate.get("limits"),
            }
        else:
            observed_contract = {"version": None, "limits": None}
        traversal_contract_variants[_canonical_json(observed_contract)] += 1
        traversal_contract_matches += int(
            observed_contract
            == {
                "version": FROZEN_TRAVERSAL_VERSION,
                "limits": FROZEN_TRAVERSAL_LIMITS,
            }
        )
    traversal["frozen_contract"] = {
        "matched": traversal_contract_matches,
        "total": len(micro_ids),
        "expected": {
            "version": FROZEN_TRAVERSAL_VERSION,
            "limits": FROZEN_TRAVERSAL_LIMITS,
        },
        "observed_variants": [
            {"contract": json.loads(contract), "count": count}
            for contract, count in sorted(traversal_contract_variants.items())
        ],
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
            "microbiology_questions": len(micro_ids),
            "non_microbiology_questions": len(non_micro_ids),
            "microbiology_question_ids": sorted(micro_ids),
            "a6a_dispatched": len(dispatch_sets["a6a"]),
            "qt4v_dispatched": len(dispatch_sets["qt4v"]),
            "qt4t_dispatched": len(dispatch_sets["qt4t"]),
        },
        "equivalence": {
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
