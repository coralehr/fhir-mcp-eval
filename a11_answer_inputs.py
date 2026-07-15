#!/usr/bin/env python3
"""Materialize the exact, blind A11 answer inputs without model calls.

The registered dataset seals the deterministic producer and governed retrieval
gate.  This module independently replays that gate, selects only the efficacy
population, and publishes exact UTF-8 model payloads plus audit-only receipts.
It never reads ``gold.jsonl`` and keeps governance and policy identifiers out of
the answer harness. Opaque synthetic FHIR resource ids and references remain in
all three model payloads because graph referential integrity is the treatment.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

import a6_packet_builder as a6
from a11_dataset_builder import (
    EFFICACY_QUESTIONS,
    FROZEN_PROFILE_SHA256,
    SOURCE_EPOCH,
    _DatasetFhirClient,
    _source_snapshot,
    verify_dataset,
)
from a11_evidence_core import canonical_bytes, sha256
from a11_governed_retrieval import build_governed_retrieval_bundle
from a11_packet_adapter import load_promoted_bundle


ANSWER_INPUTS_VERSION = "a11-answer-inputs-v1"
REGISTERED_DATASET_MANIFEST_SHA256 = (
    "442ca8d204fbd81f06e0abaf2ea5022b375deabb71a93d5ebaeccef98e99fe3c"
)
ARMS = ("v", "t", "e")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = child
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _loads(data: bytes, *, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON in {label}") from exc


def _read_json(path: Path) -> dict[str, Any]:
    value = _loads(path.read_bytes(), label=path.name)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_bytes().splitlines(), start=1):
        value = _loads(line, label=f"{path.name}:{line_number}")
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number} is not an object")
        rows.append(value)
    return rows


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(canonical_bytes(row) + b"\n" for row in rows)


def _answer_input_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    handle = io.StringIO(newline="")
    fields = ("question_id", "question", "assumption")
    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row[field] for field in fields})
    return handle.getvalue().encode("utf-8")


def _receipt(data: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _write_exclusive(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


def _producer_args() -> argparse.Namespace:
    return argparse.Namespace(
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


def _rebuild_promoted_v(
    *,
    staging: Path,
    dataset_dir: Path,
    sources: list[dict[str, Any]],
    questions: list[dict[str, Any]],
    expected_manifest_sha256: str,
):
    producer_dir = staging / "producer"
    producer_dir.mkdir()
    questions_path = producer_dir / "questions.csv"
    packet_path = producer_dir / "v_packets.jsonl"
    manifest_path = producer_dir / "v_manifest.json"
    questions_path.write_bytes((dataset_dir / "questions.csv").read_bytes())

    client = _DatasetFhirClient(sources)
    features = a6.resolve_evidence_recipe(
        a6.A11_DEPTH_AWARE_EVIDENCE_RECIPE,
        explicit_features=frozenset(),
        planner="question-only",
    )
    records: list[dict[str, Any]] = []
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
    a6.write_jsonl(packet_path, records)
    a6.write_manifest(
        manifest_path,
        input_path=questions_path,
        output_path=packet_path,
        args=_producer_args(),
        records=records,
    )
    actual_manifest_sha256 = sha256(manifest_path.read_bytes())
    if actual_manifest_sha256 != expected_manifest_sha256:
        raise ValueError("replayed V producer manifest differs from governed preflight")
    promoted = load_promoted_bundle(
        packet_path,
        manifest_path,
        expected_manifest_sha256=actual_manifest_sha256,
        expected_evidence_recipe=a6.A11_DEPTH_AWARE_EVIDENCE_RECIPE,
    )
    return promoted, actual_manifest_sha256


def materialize_answer_inputs(
    dataset_dir: Path,
    output_dir: Path,
    *,
    expected_dataset_manifest_sha256: str = REGISTERED_DATASET_MANIFEST_SHA256,
    timing_output: Path | None = None,
) -> dict[str, Any]:
    """Recompute and seal the 120 efficacy answer inputs with zero model calls."""

    dataset_dir = dataset_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"answer-input output already exists: {output_dir}")
    dataset_manifest = verify_dataset(
        dataset_dir,
        expected_manifest_sha256=expected_dataset_manifest_sha256,
    )
    sources = _read_jsonl(dataset_dir / "source_corpus.jsonl")
    questions = _read_jsonl(dataset_dir / "questions.jsonl")
    policies = _read_jsonl(dataset_dir / "policy_contexts.jsonl")
    preflight = _read_json(dataset_dir / "governed_preflight.json")
    order = _read_json(dataset_dir / "question_order.json")
    if not (
        len(sources) == len(questions) == len(policies) == preflight.get("questions")
    ):
        raise ValueError("dataset artifact coverage differs before materialization")
    by_qid = {
        question["question_id"]: (source, question, policy)
        for source, question, policy in zip(sources, questions, policies, strict=True)
    }
    preflight_by_qid = {
        row["question_id"]: row for row in preflight.get("rows", [])
    }
    ordered_all = order.get("question_ids")
    if not isinstance(ordered_all, list) or set(ordered_all) != set(by_qid):
        raise ValueError("question order does not cover the sealed dataset exactly")
    efficacy_ids = [qid for qid in ordered_all if by_qid[qid][1].get("split") == "efficacy"]
    if len(efficacy_ids) != EFFICACY_QUESTIONS or len(set(efficacy_ids)) != len(efficacy_ids):
        raise ValueError("efficacy population is not exactly 120 unique questions")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}.", dir=output_dir.parent
    ) as temporary:
        staging = Path(temporary)
        v_started_ns = time.perf_counter_ns()
        promoted, v_manifest_sha256 = _rebuild_promoted_v(
            staging=staging,
            dataset_dir=dataset_dir,
            sources=sources,
            questions=questions,
            expected_manifest_sha256=preflight["v_manifest_sha256"],
        )
        v_finished_ns = time.perf_counter_ns()

        arm_rows: dict[str, list[dict[str, Any]]] = {arm: [] for arm in ARMS}
        receipt_rows: list[dict[str, Any]] = []
        timing_rows: list[dict[str, Any]] = []
        answer_rows: list[dict[str, Any]] = []
        patient_clusters: set[str] = set()
        counts = {"answerable": 0, "unanswerable": 0}
        for question_id in efficacy_ids:
            source, question, policy = by_qid[question_id]
            expected = preflight_by_qid.get(question_id)
            if expected is None:
                raise ValueError(f"governed preflight is missing {question_id}")
            v_load_started_ns = time.perf_counter_ns()
            verified_v = promoted.load(question_id)
            v_load_finished_ns = time.perf_counter_ns()
            policy_bytes = canonical_bytes(policy)
            snapshot_bytes = canonical_bytes(_source_snapshot(source, policy))
            governed_started_ns = time.perf_counter_ns()
            governed = build_governed_retrieval_bundle(
                promoted,
                question_id,
                source_snapshot_bytes=snapshot_bytes,
                expected_snapshot_sha256=question["source_snapshot_sha256"],
                policy_context_bytes=policy_bytes,
                expected_policy_sha256=question["policy_context_sha256"],
                expected_evidence_recipe=a6.A11_DEPTH_AWARE_EVIDENCE_RECIPE,
            )
            governed_finished_ns = time.perf_counter_ns()
            v_payload_started_ns = time.perf_counter_ns()
            v_payload = verified_v["v_model_payload_json"].encode("utf-8")
            v_payload_finished_ns = time.perf_counter_ns()
            t_payload_started_ns = time.perf_counter_ns()
            t_payload = governed.load_flat_model_payload(
                question_id=question_id,
                question=question["question"],
                question_plan=question["question_plan"],
            )
            t_payload_finished_ns = time.perf_counter_ns()
            e_payload_started_ns = time.perf_counter_ns()
            e_payload = governed.load_event_group_model_payload(
                question_id=question_id,
                question=question["question"],
                question_plan=question["question_plan"],
            )
            e_payload_finished_ns = time.perf_counter_ns()
            payloads = {
                "v": v_payload,
                "t": t_payload,
                "e": e_payload,
            }
            receipt = governed.load_receipt()
            for arm, payload in payloads.items():
                expected_hash = expected[f"{arm}_model_payload_sha256"]
                if sha256(payload) != expected_hash:
                    raise ValueError(
                        f"{arm.upper()} payload differs from preflight for {question_id}"
                    )
                try:
                    payload_text = payload.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(f"{arm} payload is not UTF-8") from exc
                arm_rows[arm].append(
                    {
                        "question_id": question_id,
                        "model_payload_json": payload_text,
                        "model_payload_sha256": expected_hash,
                        "model_payload_utf8_bytes": len(payload),
                    }
                )
            if governed.receipt_sha256 != expected["governed_receipt_sha256"]:
                raise ValueError(f"governed receipt differs for {question_id}")
            shared_hash = receipt["shared_retrieval_source_sha256"]
            if shared_hash != expected["shared_retrieval_source_sha256"]:
                raise ValueError(f"shared retrieval differs for {question_id}")
            receipt_rows.append(
                {
                    "question_id": question_id,
                    "governed_receipt_sha256": governed.receipt_sha256,
                    "shared_retrieval_source_sha256": shared_hash,
                    "t_shared_retrieval_source_sha256": shared_hash,
                    "e_shared_retrieval_source_sha256": shared_hash,
                    "governed_receipt": receipt,
                    "v_integrity": verified_v["integrity"],
                }
            )
            timing_rows.append(
                {
                    "question_id": question_id,
                    "v_adapter_load_ns": v_load_finished_ns - v_load_started_ns,
                    "shared_governed_bundle_build_ns": governed_finished_ns
                    - governed_started_ns,
                    "v_payload_access_ns": v_payload_finished_ns
                    - v_payload_started_ns,
                    "t_payload_access_ns": t_payload_finished_ns
                    - t_payload_started_ns,
                    "e_payload_access_ns": e_payload_finished_ns
                    - e_payload_started_ns,
                }
            )
            answer_rows.append(
                {
                    "question_id": question_id,
                    "question": question["question"],
                    "assumption": question["assumption"],
                }
            )
            patient_clusters.add(sha256(question["patient_fhir_id"].encode("utf-8")))
            counts[
                "answerable"
                if expected["answerability_state"] == "sufficient"
                else "unanswerable"
            ] += 1

        artifact_bytes: dict[str, bytes] = {
            "answer_input.csv": _answer_input_bytes(answer_rows),
            "governed_receipts.jsonl": _jsonl_bytes(receipt_rows),
            **{
                f"{arm}_packets.jsonl": _jsonl_bytes(arm_rows[arm])
                for arm in ARMS
            },
        }
        for name, data in artifact_bytes.items():
            _write_exclusive(staging / name, data)

        artifact_paths = [
            "answer_input.csv",
            "governed_receipts.jsonl",
            "v_packets.jsonl",
            "t_packets.jsonl",
            "e_packets.jsonl",
            "producer/questions.csv",
            "producer/v_packets.jsonl",
            "producer/v_manifest.json",
        ]
        artifacts = {
            name: _receipt((staging / name).read_bytes()) for name in artifact_paths
        }
        manifest = {
            "schema_version": ANSWER_INPUTS_VERSION,
            "source_epoch": SOURCE_EPOCH,
            "model_calls": 0,
            "dataset": {
                "manifest_sha256": expected_dataset_manifest_sha256,
                "profile_sha256": FROZEN_PROFILE_SHA256,
                "artifacts": dataset_manifest["artifacts"],
                "compiler_dependencies": dataset_manifest["compiler_dependencies"],
            },
            "arms": list(ARMS),
            "question_count": len(efficacy_ids),
            "question_ids": efficacy_ids,
            "question_ids_sha256": sha256(canonical_bytes(efficacy_ids)),
            "patient_clusters": len(patient_clusters),
            "patient_cluster_sha256s_sha256": sha256(
                canonical_bytes(sorted(patient_clusters))
            ),
            "population": counts,
            "v_producer_manifest_sha256": v_manifest_sha256,
            "artifacts": artifacts,
            "materializer": _receipt(Path(__file__).resolve().read_bytes()),
            "all_checks_passed": True,
        }
        _write_exclusive(staging / "manifest.json", _json_bytes(manifest))
        _write_exclusive(
            staging / "manifest.sha256",
            (sha256((staging / "manifest.json").read_bytes()) + "\n").encode("ascii"),
        )
        os.replace(staging, output_dir)

    verified = verify_answer_inputs(
        output_dir,
        expected_dataset_manifest_sha256=expected_dataset_manifest_sha256,
    )
    if timing_output is not None:
        timing_output = timing_output.resolve()
        if timing_output.exists():
            raise FileExistsError(f"timing output already exists: {timing_output}")
        timing = {
            "schema_version": "a11-compilation-timing-v1",
            "clock": "time.perf_counter_ns",
            "model_calls": 0,
            "dataset_manifest_sha256": expected_dataset_manifest_sha256,
            "answer_inputs_manifest_sha256": sha256(
                (output_dir / "manifest.json").read_bytes()
            ),
            "v_full_144_production_ns": v_finished_ns - v_started_ns,
            "efficacy_question_count": len(timing_rows),
            "rows": timing_rows,
            "attribution_note": (
                "shared_governed_bundle_build_ns includes authorized traversal plus "
                "the bound T serialization and E compilation performed by the sealed factory; "
                "payload_access_ns measures verified immutable-byte retrieval only"
            ),
        }
        _write_exclusive(timing_output, _json_bytes(timing))
    return verified


def verify_answer_inputs(
    output_dir: Path,
    *,
    expected_dataset_manifest_sha256: str = REGISTERED_DATASET_MANIFEST_SHA256,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise ValueError("answer-input directory is unsafe")
    manifest_path = output_dir / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = sha256(manifest_bytes)
    if (output_dir / "manifest.sha256").read_text(encoding="ascii") != manifest_sha256 + "\n":
        raise ValueError("answer-input manifest sidecar changed")
    manifest = _read_json(manifest_path)
    if (
        manifest.get("schema_version") != ANSWER_INPUTS_VERSION
        or manifest.get("model_calls") != 0
        or manifest.get("all_checks_passed") is not True
    ):
        raise ValueError("answer-input manifest contract changed")
    if manifest.get("dataset", {}).get("manifest_sha256") != expected_dataset_manifest_sha256:
        raise ValueError("answer inputs do not bind the registered dataset")
    if manifest.get("arms") != list(ARMS):
        raise ValueError("answer-input arm order changed")
    question_ids = manifest.get("question_ids")
    if (
        not isinstance(question_ids, list)
        or len(question_ids) != EFFICACY_QUESTIONS
        or len(set(question_ids)) != EFFICACY_QUESTIONS
        or sha256(canonical_bytes(question_ids)) != manifest.get("question_ids_sha256")
    ):
        raise ValueError("answer-input efficacy coverage changed")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("answer-input artifact inventory is missing")
    for name, expected in artifacts.items():
        path = output_dir / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"answer-input artifact is unsafe: {name}")
        if _receipt(path.read_bytes()) != expected:
            raise ValueError(f"answer-input artifact changed: {name}")
    for arm in ARMS:
        rows = _read_jsonl(output_dir / f"{arm}_packets.jsonl")
        if [row.get("question_id") for row in rows] != question_ids:
            raise ValueError(f"{arm} payload coverage changed")
        for row in rows:
            if set(row) != {
                "question_id",
                "model_payload_json",
                "model_payload_sha256",
                "model_payload_utf8_bytes",
            }:
                raise ValueError(f"{arm} payload record fields changed")
            payload = row["model_payload_json"].encode("utf-8")
            if _receipt(payload) != {
                "sha256": row["model_payload_sha256"],
                "bytes": row["model_payload_utf8_bytes"],
            }:
                raise ValueError(f"{arm} payload record changed")
    receipts = _read_jsonl(output_dir / "governed_receipts.jsonl")
    if [row.get("question_id") for row in receipts] != question_ids:
        raise ValueError("governed receipt coverage changed")
    for row in receipts:
        shared = row.get("shared_retrieval_source_sha256")
        if not (
            shared
            == row.get("t_shared_retrieval_source_sha256")
            == row.get("e_shared_retrieval_source_sha256")
            == row.get("governed_receipt", {}).get(
                "shared_retrieval_source_sha256"
            )
        ):
            raise ValueError("T/E shared retrieval receipt diverged")
        if sha256(canonical_bytes(row["governed_receipt"])) != row.get(
            "governed_receipt_sha256"
        ):
            raise ValueError("governed receipt hash changed")
    with (output_dir / "answer_input.csv").open(newline="", encoding="utf-8") as handle:
        answer_rows = list(csv.DictReader(handle))
    if (
        not answer_rows
        or list(answer_rows[0]) != ["question_id", "question", "assumption"]
        or [row["question_id"] for row in answer_rows] != question_ids
    ):
        raise ValueError("blind answer input changed")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--expected-dataset-manifest-sha256",
        default=REGISTERED_DATASET_MANIFEST_SHA256,
    )
    parser.add_argument("--timing-output", type=Path, default=None)
    args = parser.parse_args()
    manifest = materialize_answer_inputs(
        args.dataset_dir,
        args.output_dir,
        expected_dataset_manifest_sha256=args.expected_dataset_manifest_sha256,
        timing_output=args.timing_output,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
