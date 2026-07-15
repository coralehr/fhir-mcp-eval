#!/usr/bin/env python3
"""Governed, version-bound retrieval packets for the zero-model A11 gate.

The factory consumes an already verified :class:`PromotedBundle`, a separately
pinned synthetic source snapshot, and an explicit policy context.  It projects
the registered traversal once, stores that retrieval source as immutable
canonical bytes, and derives fresh-copy T and E packets from the same bytes.

This module is benchmark governance for synthetic data.  It is not a Bonfire
authorization implementation and makes no model or network calls.
"""

from __future__ import annotations

import copy
import json
import math
import re
from typing import Any

import a6_packet_builder as a6
from a11_evidence_core import (
    CORE_VERSION,
    canonical_bytes,
    project_traversal,
    resource_ref,
    sha256,
)
from a11_event_group_benchmark import (
    EVENT_GROUP_COMPILER_VERSION,
    compile_event_groups,
)
from a11_packet_adapter import ADAPTER_VERSION, PromotedBundle


GOVERNED_RETRIEVAL_VERSION = "a11-governed-retrieval-v1"
SOURCE_SNAPSHOT_VERSION = "a11-synthetic-source-snapshot-v1"
AUTHORIZATION_DECISION_VERSION = "a11-benchmark-authorization-v1"
MAX_SOURCE_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_POLICY_CONTEXT_BYTES = 64 * 1024
MAX_SOURCE_RESOURCES = 4_096
MAX_SEED_ROOTS = 256
MAX_TRAVERSAL_DEPTH = 3
MAX_TRAVERSAL_TARGETS = 1_000
MAX_MODEL_PACKET_BYTES = 1024 * 1024

_SNAPSHOT_FIELDS = frozenset(
    {
        "schema_version",
        "source_id",
        "source_version",
        "practice_id",
        "patient_ref",
        "resources",
    }
)
_SOURCE_ENTRY_FIELDS = frozenset({"practice_id", "resource"})
_POLICY_FIELDS = frozenset(
    {
        "principal_id",
        "practice_id",
        "purpose",
        "allowed_purposes",
        "patient_ref",
        "source_id",
        "source_version",
        "traversal_bounds",
    }
)
_BOUND_FIELDS = frozenset(
    {
        "max_depth",
        "max_targets",
        "max_packet_bytes",
        "vocabulary_allowed_resource_types",
    }
)
_VERIFIED_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "evidence_recipe",
        "question_id",
        "question",
        "patient_fhir_id",
        "patient_ref",
        "assumption",
        "intent",
        "question_plan",
        "packet",
        "root_refs",
        "v_model_payload",
        "v_model_payload_json",
        "integrity",
    }
)
_REGISTERED_PATH_RELATIONS = frozenset(
    {
        "Observation.hasMember",
        "Observation.specimen",
        "DiagnosticReport.result",
    }
)
_FORBIDDEN_SOURCE_KEYS = frozenset(
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
        "reference_answer",
        "true_answer",
        "true_fhir_ids",
    }
)
_FORBIDDEN_SOURCE_PREFIXES = ("expected_", "gold_", "true_")
_BUNDLE_FACTORY_TOKEN = object()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    )


def _hash_text(value: str) -> str:
    return sha256(value.encode("utf-8"))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key}")
        value[key] = child
    return value


def _reject_non_finite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _load_json_bytes(data: bytes, *, label: str) -> Any:
    if not isinstance(data, bytes):
        raise TypeError(f"{label} must be immutable bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} is not UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON") from exc


def _reject_non_finite_numbers(value: Any, *, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite number is forbidden at {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_non_finite_numbers(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_non_finite_numbers(child, path=f"{path}[{index}]")


def _fresh_copy(data: bytes) -> dict[str, Any]:
    value = _load_json_bytes(data, label="sealed bundle artifact")
    if not isinstance(value, dict):
        raise RuntimeError("sealed bundle artifact is not an object")
    return value


def _required_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _reject_benchmark_fields(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        forbidden = {
            str(key)
            for key in value
            if str(key).lower() in _FORBIDDEN_SOURCE_KEYS
            or str(key).lower().startswith(_FORBIDDEN_SOURCE_PREFIXES)
        }
        if forbidden:
            raise ValueError(
                f"synthetic source contains benchmark-only fields at {path}: "
                + ",".join(sorted(forbidden))
            )
        for key, child in value.items():
            _reject_benchmark_fields(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_benchmark_fields(child, path=f"{path}[{index}]")


def _validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(policy, dict) or set(policy) != _POLICY_FIELDS:
        raise ValueError("policy context fields changed")
    for field in (
        "principal_id",
        "practice_id",
        "purpose",
        "patient_ref",
        "source_id",
        "source_version",
    ):
        _required_string(policy.get(field), field=f"policy.{field}")
    if re.fullmatch(r"Patient/[A-Za-z0-9\-.]{1,64}", policy["patient_ref"]) is None:
        raise ValueError("policy.patient_ref must be one relative Patient reference")

    allowed = policy.get("allowed_purposes")
    if (
        not isinstance(allowed, list)
        or not allowed
        or any(not isinstance(item, str) or not item for item in allowed)
        or len(allowed) != len(set(allowed))
        or allowed != sorted(allowed)
    ):
        raise ValueError("policy.allowed_purposes must be a sorted unique string list")
    if policy["purpose"] not in allowed:
        raise PermissionError("policy purpose is denied")

    bounds = policy.get("traversal_bounds")
    if not isinstance(bounds, dict) or set(bounds) != _BOUND_FIELDS:
        raise ValueError("policy traversal bound fields changed")
    max_depth = bounds.get("max_depth")
    max_targets = bounds.get("max_targets")
    max_packet_bytes = bounds.get("max_packet_bytes")
    if (
        not isinstance(max_depth, int)
        or isinstance(max_depth, bool)
        or not 2 <= max_depth <= MAX_TRAVERSAL_DEPTH
    ):
        raise ValueError("policy max_depth is outside the A11 bound")
    if (
        not isinstance(max_targets, int)
        or isinstance(max_targets, bool)
        or not 1 <= max_targets <= MAX_TRAVERSAL_TARGETS
    ):
        raise ValueError("policy max_targets is outside the A11 bound")
    if (
        not isinstance(max_packet_bytes, int)
        or isinstance(max_packet_bytes, bool)
        or not 256 <= max_packet_bytes <= MAX_MODEL_PACKET_BYTES
    ):
        raise ValueError("policy max_packet_bytes is outside the A11 bound")
    if bounds.get("vocabulary_allowed_resource_types") != [
        "Observation",
        "Specimen",
    ]:
        raise ValueError("policy traversal resource vocabulary changed")
    return copy.deepcopy(policy)


def _load_pinned_policy(
    policy_context_bytes: bytes,
    *,
    expected_policy_sha256: str,
) -> dict[str, Any]:
    if not _is_sha256(expected_policy_sha256):
        raise ValueError("expected_policy_sha256 must be a lowercase sha256")
    if not isinstance(policy_context_bytes, bytes):
        raise TypeError("policy_context_bytes must be immutable bytes")
    if len(policy_context_bytes) > MAX_POLICY_CONTEXT_BYTES:
        raise ValueError("policy context exceeds the A11 byte bound")
    if sha256(policy_context_bytes) != expected_policy_sha256:
        raise ValueError("policy context does not match the pinned sha256")
    policy = _load_json_bytes(policy_context_bytes, label="policy context")
    if not isinstance(policy, dict):
        raise ValueError("policy context must be an object")
    if canonical_bytes(policy) != policy_context_bytes:
        raise ValueError("policy context must use canonical JSON bytes")
    return _validate_policy(policy)


def _version_id(resource: dict[str, Any], *, reference: str) -> str:
    meta = resource.get("meta")
    version = meta.get("versionId") if isinstance(meta, dict) else None
    if not isinstance(version, str) or not version:
        raise ValueError(f"source resource has no versionId: {reference}")
    if a6.FHIR_ID_PATTERN.fullmatch(version) is None:
        raise ValueError(f"source resource has an invalid versionId: {reference}")
    return version


def _validate_snapshot(
    snapshot_bytes: bytes,
    *,
    expected_snapshot_sha256: str,
    policy: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if not _is_sha256(expected_snapshot_sha256):
        raise ValueError("expected_snapshot_sha256 must be a lowercase sha256")
    if not isinstance(snapshot_bytes, bytes):
        raise TypeError("source_snapshot_bytes must be immutable bytes")
    if len(snapshot_bytes) > MAX_SOURCE_SNAPSHOT_BYTES:
        raise ValueError("source snapshot exceeds the A11 byte bound")
    if sha256(snapshot_bytes) != expected_snapshot_sha256:
        raise ValueError("source snapshot does not match the pinned sha256")
    snapshot = _load_json_bytes(snapshot_bytes, label="source snapshot")
    if not isinstance(snapshot, dict) or set(snapshot) != _SNAPSHOT_FIELDS:
        raise ValueError("source snapshot envelope fields changed")
    if snapshot.get("schema_version") != SOURCE_SNAPSHOT_VERSION:
        raise ValueError("unsupported source snapshot schema")
    for field in (
        "source_id",
        "source_version",
        "practice_id",
        "patient_ref",
    ):
        _required_string(snapshot.get(field), field=f"source snapshot {field}")
    for field in ("source_id", "source_version", "practice_id", "patient_ref"):
        if snapshot[field] != policy[field]:
            raise PermissionError(f"source snapshot {field} does not match policy")

    entries = snapshot.get("resources")
    if (
        not isinstance(entries, list)
        or not entries
        or len(entries) > MAX_SOURCE_RESOURCES
    ):
        raise ValueError("source snapshot resources are outside the A11 bound")
    _reject_benchmark_fields(entries, path="source.resources")
    index: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _SOURCE_ENTRY_FIELDS:
            raise ValueError("source resource envelope fields changed")
        _required_string(entry.get("practice_id"), field="source resource practice_id")
        resource = entry.get("resource")
        if not isinstance(resource, dict):
            raise ValueError("source resource is not an object")
        resource_type = resource.get("resourceType")
        resource_id = resource.get("id")
        if (
            not isinstance(resource_type, str)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", resource_type) is None
            or not isinstance(resource_id, str)
            or a6.FHIR_ID_PATTERN.fullmatch(resource_id) is None
        ):
            raise ValueError("source resource has an invalid identity")
        reference = resource_ref(resource)
        if reference in index:
            raise ValueError(f"duplicate source resource identity: {reference}")
        _version_id(resource, reference=reference)
        index[reference] = resource
    return snapshot, index


def _validate_question_plan(record: dict[str, Any]) -> None:
    question = _required_string(record.get("question"), field="verified record question")
    plan = record.get("question_plan")
    if not isinstance(plan, dict):
        raise ValueError("verified record has no question plan")
    if plan.get("question_sha256") != _hash_text(question):
        raise ValueError("verified record question plan hash changed")
    if plan.get("temporal_policy") not in {"first", "latest"}:
        raise ValueError("verified record temporal policy changed")
    signatures = plan.get("path_signatures")
    if not isinstance(signatures, list) or not signatures:
        raise ValueError("verified record has no path signatures")
    for signature in signatures:
        if (
            not isinstance(signature, list)
            or not 2 <= len(signature) <= MAX_TRAVERSAL_DEPTH
            or any(relation not in _REGISTERED_PATH_RELATIONS for relation in signature)
            or signature[0]
            not in {"Observation.hasMember", "DiagnosticReport.result"}
        ):
            raise ValueError("verified record has an unregistered path signature")


def _validate_verified_record(
    record: dict[str, Any],
    *,
    expected_evidence_recipe: str,
) -> None:
    _reject_non_finite_numbers(record, path="verified_record")
    if not isinstance(record, dict) or set(record) != _VERIFIED_RECORD_FIELDS:
        raise ValueError("verified adapter record fields changed")
    if record.get("schema_version") != ADAPTER_VERSION:
        raise ValueError("verified adapter record version changed")
    if record.get("evidence_recipe") != expected_evidence_recipe:
        raise ValueError("verified record evidence recipe changed")
    planner_version = a6.question_only_planner_version(expected_evidence_recipe)
    if planner_version != a6.A11_QO_PLANNER_VERSION:
        raise ValueError("expected recipe is not an A11 four-family root recipe")
    if record.get("intent", {}).get("planner") != planner_version:
        raise ValueError("verified record root planner changed")
    if record.get("packet", {}).get("planner") != planner_version:
        raise ValueError("verified packet root planner changed")
    _validate_question_plan(record)

    integrity = record.get("integrity")
    if not isinstance(integrity, dict):
        raise ValueError("verified record has no integrity receipt")
    required_hashes = (
        "adapter_sha256",
        "manifest_sha256",
        "packet_file_sha256",
        "record_sha256",
        "packet_sha256",
        "model_payload_sha256",
        "root_refs_sha256",
    )
    if any(not _is_sha256(integrity.get(field)) for field in required_hashes):
        raise ValueError("verified record integrity receipt is invalid")
    root_refs = record.get("root_refs")
    if (
        not isinstance(root_refs, list)
        or not root_refs
        or any(not isinstance(ref, str) for ref in root_refs)
        or len(root_refs) > MAX_SEED_ROOTS
        or root_refs != sorted(set(root_refs))
        or sha256(canonical_bytes(root_refs)) != integrity["root_refs_sha256"]
    ):
        raise ValueError("verified V roots changed")
    packet = record.get("packet")
    if not isinstance(packet, dict):
        raise ValueError("verified record packet changed")
    packet_hash_input = {key: value for key, value in packet.items() if key != "sha256"}
    packet_hash = sha256(canonical_bytes(packet_hash_input))
    if packet.get("sha256") != packet_hash or integrity["packet_sha256"] != packet_hash:
        raise ValueError("verified V packet hash changed")
    original_record = {
        "question_id": record["question_id"],
        "question": record["question"],
        "patient_fhir_id": record["patient_fhir_id"],
        "assumption": record["assumption"],
        "intent": record["intent"],
        "packet": record["packet"],
    }
    if sha256(canonical_bytes(original_record)) != integrity["record_sha256"]:
        raise ValueError("verified V record hash changed")
    rendered_payload = record.get("v_model_payload_json")
    if (
        not isinstance(rendered_payload, str)
        or _hash_text(rendered_payload) != integrity["model_payload_sha256"]
    ):
        raise ValueError("verified V model payload hash changed")
    try:
        parsed_payload = json.loads(
            rendered_payload, object_pairs_hook=_unique_object
        )
    except json.JSONDecodeError as exc:
        raise ValueError("verified V model payload JSON changed") from exc
    if parsed_payload != record.get("v_model_payload"):
        raise ValueError("verified V model payload changed")


def _validate_roots_against_source(
    record: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    source_index: dict[str, dict[str, Any]],
) -> None:
    v_resources = {
        resource_ref(resource): resource
        for resource in record["packet"].get("resources", [])
        if isinstance(resource, dict)
    }
    source_practice = snapshot["practice_id"]
    entries = {
        resource_ref(entry["resource"]): entry
        for entry in snapshot["resources"]
    }
    for root_ref in record["root_refs"]:
        source_entry = entries.get(root_ref)
        if source_entry is None or source_entry["practice_id"] != source_practice:
            raise PermissionError(f"verified V root is unavailable in source: {root_ref}")
        v_root = v_resources.get(root_ref)
        source_root = source_index[root_ref]
        if (
            v_root is None
            or a6.project_resource(source_root) != a6.project_resource(v_root)
        ):
            raise ValueError(f"verified V root differs from source snapshot: {root_ref}")


def _resource_version_rows(source_packet: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for resource in source_packet["resources"]:
        reference = resource_ref(resource)
        rows.append(
            {
                "reference": reference,
                "version_id": _version_id(resource, reference=reference),
            }
        )
    return sorted(rows, key=canonical_bytes)


def _patient_references(resource: dict[str, Any]) -> set[str]:
    references: set[str] = set()
    for field in ("subject", "patient", "beneficiary"):
        value = resource.get(field)
        if isinstance(value, dict) and isinstance(value.get("reference"), str):
            reference = value["reference"]
            if reference.startswith("Patient/"):
                references.add(reference)
    return references


def _validate_included_patient_scope(
    source_packet: dict[str, Any],
    *,
    source_index: dict[str, dict[str, Any]],
    patient_ref: str,
) -> None:
    """Fail closed when a fetched clinical resource lacks an exact patient bind."""

    for projected in source_packet["resources"]:
        reference = resource_ref(projected)
        source = source_index[reference]
        if source.get("resourceType") == "Patient":
            if reference != patient_ref:
                raise PermissionError("traversal included a different Patient resource")
            continue
        if _patient_references(source) != {patient_ref}:
            raise PermissionError(
                f"traversal resource is not explicitly patient-bound: {reference}"
            )


class GovernedRetrievalBundle:
    """Factory-only canonical A11 T/E artifacts with verified public views."""

    __slots__ = (
        "_retrieval_source_bytes",
        "_flat_packet_bytes",
        "_event_group_packet_bytes",
        "_receipt_bytes",
        "_sealed",
    )

    def __init__(
        self,
        *,
        retrieval_source_bytes: bytes = b"",
        flat_packet_bytes: bytes = b"",
        event_group_packet_bytes: bytes = b"",
        receipt_bytes: bytes = b"",
        _factory_token: object | None = None,
    ) -> None:
        if _factory_token is not _BUNDLE_FACTORY_TOKEN:
            raise TypeError("use build_governed_retrieval_bundle")
        object.__setattr__(self, "_retrieval_source_bytes", bytes(retrieval_source_bytes))
        object.__setattr__(self, "_flat_packet_bytes", bytes(flat_packet_bytes))
        object.__setattr__(
            self, "_event_group_packet_bytes", bytes(event_group_packet_bytes)
        )
        object.__setattr__(self, "_receipt_bytes", bytes(receipt_bytes))
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("governed retrieval bundle is immutable")
        object.__setattr__(self, name, value)

    @property
    def retrieval_source_sha256(self) -> str:
        return sha256(self._retrieval_source_bytes)

    @property
    def receipt_sha256(self) -> str:
        return sha256(self._receipt_bytes)

    def _verified_receipt(self) -> dict[str, Any]:
        receipt = _fresh_copy(self._receipt_bytes)
        if receipt.get("shared_retrieval_source_sha256") != sha256(
            self._retrieval_source_bytes
        ):
            raise RuntimeError("governed retrieval source diverged from receipt")
        packets = receipt.get("model_packets")
        if (
            not isinstance(packets, dict)
            or packets.get("t_sha256") != sha256(self._flat_packet_bytes)
            or packets.get("e_sha256") != sha256(self._event_group_packet_bytes)
        ):
            raise RuntimeError("governed model packet diverged from receipt")
        return receipt

    def _verify_question_binding(
        self,
        *,
        question_id: str,
        question: str,
        question_plan: dict[str, Any],
    ) -> None:
        binding = self._verified_receipt().get("question_binding")
        if (
            not isinstance(binding, dict)
            or binding.get("question_id_sha256") != _hash_text(question_id)
            or binding.get("question_sha256") != _hash_text(question)
            or binding.get("question_plan_sha256")
            != sha256(canonical_bytes(question_plan))
        ):
            raise ValueError("question does not match governed packet binding")

    def load_flat_model_payload(
        self,
        *,
        question_id: str,
        question: str,
        question_plan: dict[str, Any],
    ) -> bytes:
        """Return the exact immutable UTF-8 payload bound to the T question."""

        self._verify_question_binding(
            question_id=question_id,
            question=question,
            question_plan=question_plan,
        )
        return bytes(self._flat_packet_bytes)

    def load_event_group_model_payload(
        self,
        *,
        question_id: str,
        question: str,
        question_plan: dict[str, Any],
    ) -> bytes:
        """Return the exact immutable UTF-8 payload bound to the E question."""

        self._verify_question_binding(
            question_id=question_id,
            question=question,
            question_plan=question_plan,
        )
        return bytes(self._event_group_packet_bytes)

    def load_flat_model_packet(self) -> dict[str, Any]:
        """Return a mutable audit/test view; runners must use the bytes API."""

        self._verified_receipt()
        return _fresh_copy(self._flat_packet_bytes)

    def load_event_group_model_packet(self) -> dict[str, Any]:
        """Return a mutable audit/test view; runners must use the bytes API."""

        self._verified_receipt()
        return _fresh_copy(self._event_group_packet_bytes)

    def load_audit_retrieval_source(self) -> dict[str, Any]:
        """Return a fresh audit-only view with requested and resolved path steps."""

        self._verified_receipt()
        return _fresh_copy(self._retrieval_source_bytes)

    def load_receipt(self) -> dict[str, Any]:
        return self._verified_receipt()


def build_governed_retrieval_bundle(
    promoted_bundle: PromotedBundle,
    question_id: str,
    *,
    source_snapshot_bytes: bytes,
    expected_snapshot_sha256: str,
    policy_context_bytes: bytes,
    expected_policy_sha256: str,
    expected_evidence_recipe: str = a6.A11_EVIDENCE_RECIPE,
) -> GovernedRetrievalBundle:
    """Verify policy/source bindings and seal one shared A11 T/E retrieval."""

    if not isinstance(promoted_bundle, PromotedBundle):
        raise TypeError("promoted_bundle must be a verified PromotedBundle")
    _required_string(question_id, field="question_id")
    if promoted_bundle.evidence_recipe != expected_evidence_recipe:
        raise ValueError("promoted bundle evidence recipe does not match expectation")
    record = promoted_bundle.load(question_id)
    _validate_verified_record(
        record, expected_evidence_recipe=expected_evidence_recipe
    )
    policy = _load_pinned_policy(
        policy_context_bytes,
        expected_policy_sha256=expected_policy_sha256,
    )
    if record["patient_ref"] != policy["patient_ref"]:
        raise PermissionError("verified V patient does not match policy")
    snapshot, source_index = _validate_snapshot(
        source_snapshot_bytes,
        expected_snapshot_sha256=expected_snapshot_sha256,
        policy=policy,
    )
    _validate_roots_against_source(
        record, snapshot=snapshot, source_index=source_index
    )

    bounds = policy["traversal_bounds"]
    case = {
        "case_id": question_id,
        "principal": {
            "principal_id": policy["principal_id"],
            "practice_id": policy["practice_id"],
            "purpose": policy["purpose"],
        },
        "allowed_purposes": list(policy["allowed_purposes"]),
        "patient_ref": policy["patient_ref"],
        "seed_refs": list(record["root_refs"]),
        "resources": snapshot["resources"],
        "max_depth": bounds["max_depth"],
        "max_targets": bounds["max_targets"],
        "max_packet_bytes": bounds["max_packet_bytes"],
        "vocabulary_allowed_resource_types": list(
            bounds["vocabulary_allowed_resource_types"]
        ),
    }
    source_packet = project_traversal(case)
    if source_packet["root_refs"] != record["root_refs"]:
        raise PermissionError("not every verified V root is authorized and available")
    _validate_included_patient_scope(
        source_packet,
        source_index=source_index,
        patient_ref=policy["patient_ref"],
    )

    version_rows = _resource_version_rows(source_packet)
    retrieval_source = {
        "root_refs": source_packet["root_refs"],
        "resources": source_packet["resources"],
        "path_citations": source_packet["path_citations"],
        "audit_path_citations": source_packet["audit_path_citations"],
        "bounds": source_packet["bounds"],
    }
    retrieval_source_bytes = canonical_bytes(retrieval_source)
    sealed_source_for_t = _fresh_copy(retrieval_source_bytes)
    flat_packet = {
        "resources": sealed_source_for_t["resources"],
        "path_citations": sealed_source_for_t["path_citations"],
    }
    event_group_packet = compile_event_groups(
        _fresh_copy(retrieval_source_bytes),
        copy.deepcopy(record["question_plan"]),
    )
    flat_packet_bytes = canonical_bytes(flat_packet)
    event_group_packet_bytes = canonical_bytes(event_group_packet)
    max_packet_bytes = bounds["max_packet_bytes"]
    if (
        len(flat_packet_bytes) > max_packet_bytes
        or len(event_group_packet_bytes) > max_packet_bytes
    ):
        raise ValueError("A11 model packet exceeds the frozen byte bound")

    authorization_context = {
        "principal_id": policy["principal_id"],
        "practice_id": policy["practice_id"],
        "purpose": policy["purpose"],
        "allowed_purposes": policy["allowed_purposes"],
        "patient_ref": policy["patient_ref"],
        "source_id": policy["source_id"],
        "source_version": policy["source_version"],
        "traversal_bounds": policy["traversal_bounds"],
    }
    authorization_context_sha256 = sha256(canonical_bytes(authorization_context))
    authorization_decision = {
        "version": AUTHORIZATION_DECISION_VERSION,
        "decision": "allow",
        "context_sha256": authorization_context_sha256,
    }
    integrity = record["integrity"]
    resource_refs = sorted(row["reference"] for row in version_rows)
    resource_version_bindings = [
        {
            "resource_ref_sha256": _hash_text(row["reference"]),
            "version_id_sha256": _hash_text(row["version_id"]),
            "binding_sha256": sha256(canonical_bytes(row)),
        }
        for row in version_rows
    ]
    receipt = {
        "schema_version": GOVERNED_RETRIEVAL_VERSION,
        "evidence_recipe": expected_evidence_recipe,
        "question_binding": {
            "question_id_sha256": _hash_text(record["question_id"]),
            "question_sha256": _hash_text(record["question"]),
            "question_plan_sha256": sha256(
                canonical_bytes(record["question_plan"])
            ),
        },
        "authorization": {
            "decision": "allow",
            "decision_sha256": sha256(canonical_bytes(authorization_decision)),
            "context_sha256": authorization_context_sha256,
            "policy_artifact_sha256": expected_policy_sha256,
            "principal_id_sha256": _hash_text(policy["principal_id"]),
            "practice_id_sha256": _hash_text(policy["practice_id"]),
            "patient_ref_sha256": _hash_text(policy["patient_ref"]),
            "purpose_sha256": _hash_text(policy["purpose"]),
            "allowed_purposes_sha256": sha256(
                canonical_bytes(policy["allowed_purposes"])
            ),
        },
        "source": {
            "source_id_sha256": _hash_text(policy["source_id"]),
            "source_version": policy["source_version"],
            "source_version_sha256": _hash_text(policy["source_version"]),
            "snapshot_sha256": expected_snapshot_sha256,
        },
        "v": {
            "adapter_version": ADAPTER_VERSION,
            "adapter_sha256": integrity["adapter_sha256"],
            "manifest_sha256": integrity["manifest_sha256"],
            "packet_file_sha256": integrity["packet_file_sha256"],
            "record_sha256": integrity["record_sha256"],
            "packet_sha256": integrity["packet_sha256"],
            "model_payload_sha256": integrity["model_payload_sha256"],
            "root_refs_sha256": integrity["root_refs_sha256"],
        },
        "traversal": {
            "core_version": CORE_VERSION,
            "event_group_compiler_version": EVENT_GROUP_COMPILER_VERSION,
            "root_refs_sha256": sha256(canonical_bytes(source_packet["root_refs"])),
            "resource_refs_sha256": sha256(canonical_bytes(resource_refs)),
            "resource_versions_sha256": sha256(canonical_bytes(version_rows)),
            "resource_version_bindings": resource_version_bindings,
            "path_citations_sha256": sha256(
                canonical_bytes(source_packet["audit_path_citations"])
            ),
            "bounds_sha256": sha256(canonical_bytes(source_packet["bounds"])),
        },
        "shared_retrieval_source_sha256": sha256(retrieval_source_bytes),
        "model_packets": {
            "encoding": "canonical-json-utf8-v1",
            "t_sha256": sha256(flat_packet_bytes),
            "t_utf8_bytes": len(flat_packet_bytes),
            "e_sha256": sha256(event_group_packet_bytes),
            "e_utf8_bytes": len(event_group_packet_bytes),
        },
    }
    receipt_bytes = canonical_bytes(receipt)
    return GovernedRetrievalBundle(
        retrieval_source_bytes=retrieval_source_bytes,
        flat_packet_bytes=flat_packet_bytes,
        event_group_packet_bytes=event_group_packet_bytes,
        receipt_bytes=receipt_bytes,
        _factory_token=_BUNDLE_FACTORY_TOKEN,
    )
