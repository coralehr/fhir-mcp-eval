"""Versioned answer-state contract for A11b successor experiments.

The historical r3 preview used a prose sentinel to distinguish substantive
answers from insufficiency.  This module makes that state categorical so the
prompt, schema, grader, and behavior metrics cannot infer different meanings
from answer wording.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


CONTRACT_VERSION = "a11b-answer-contract-v2"
ADAPTATION_RECEIPT_VERSION = "a11b-answer-adaptation-receipt-v1"
ANSWERED = "answered"
INSUFFICIENT = "insufficient"
FIELDS = frozenset(
    {
        "status",
        "answer",
        "source_resource_ids",
        "evidence_summary",
        "insufficiency_reason",
    }
)
_FHIR_REFERENCE = re.compile(r"^[A-Z][A-Za-z0-9]*/[A-Za-z0-9.-]{1,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_ANSWER_BYTES = 128
MAX_EVIDENCE_SUMMARY_BYTES = 1024
MAX_INSUFFICIENCY_REASON_BYTES = 1024
MAX_SOURCE_RESOURCE_IDS = 16
MAX_SOURCE_RESOURCE_ID_BYTES = 128


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _bounded_nonempty(value: object, *, byte_cap: int) -> bool:
    return _nonempty(value) and len(value.encode("utf-8")) <= byte_cap


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = child
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def validate_answer(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return one validated answer or fail closed on ambiguous state."""

    if not isinstance(value, Mapping) or set(value) != FIELDS:
        raise ValueError("answer fields do not match the v2 contract")
    status = value.get("status")
    if status not in {ANSWERED, INSUFFICIENT}:
        raise ValueError("answer status must be answered or insufficient")
    evidence_summary = value.get("evidence_summary")
    if not _bounded_nonempty(evidence_summary, byte_cap=MAX_EVIDENCE_SUMMARY_BYTES):
        raise ValueError("evidence_summary is empty or oversized")
    sources = value.get("source_resource_ids")
    if (
        not isinstance(sources, list)
        or len(sources) > MAX_SOURCE_RESOURCE_IDS
        or any(
            not isinstance(source, str)
            or len(source.encode("utf-8")) > MAX_SOURCE_RESOURCE_ID_BYTES
            or _FHIR_REFERENCE.fullmatch(source) is None
            for source in sources
        )
        or len(sources) != len(set(sources))
    ):
        raise ValueError("source_resource_ids must be unique FHIR references")

    answer = value.get("answer")
    reason = value.get("insufficiency_reason")
    if status == ANSWERED:
        if (
            not _bounded_nonempty(answer, byte_cap=MAX_ANSWER_BYTES)
            or not sources
            or reason is not None
        ):
            raise ValueError(
                "answered state requires an answer, citations, and null reason"
            )
    elif answer is not None or not _bounded_nonempty(
        reason, byte_cap=MAX_INSUFFICIENCY_REASON_BYTES
    ):
        raise ValueError(
            "insufficient state requires null answer and a nonempty reason"
        )
    return dict(value)


def adapt_transport_answer(payload: bytes) -> dict[str, Any]:
    """Validate raw provider bytes and record their lossless canonical mapping."""

    if not isinstance(payload, bytes) or not payload:
        raise ValueError("transport answer must be nonempty bytes")
    try:
        transport = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("transport answer is invalid JSON") from exc
    if not isinstance(transport, Mapping):
        raise ValueError("transport answer must be an object")
    canonical_answer = validate_answer(transport)
    canonical_transport = _canonical_bytes(transport)
    canonical_answer_bytes = _canonical_bytes(canonical_answer)
    record = {
        "transport_payload": dict(transport),
        "canonical_answer": canonical_answer,
        "normalization_receipt": {
            "schema_version": ADAPTATION_RECEIPT_VERSION,
            "contract_version": CONTRACT_VERSION,
            "adaptation": "identity",
            "lossless": canonical_transport == canonical_answer_bytes,
            "raw_transport_sha256": hashlib.sha256(payload).hexdigest(),
            "canonical_transport_sha256": hashlib.sha256(
                canonical_transport
            ).hexdigest(),
            "canonical_answer_sha256": hashlib.sha256(
                canonical_answer_bytes
            ).hexdigest(),
        },
    }
    return validate_adaptation_record(record)


def validate_adaptation_record(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a recorded transport-to-canonical identity adaptation."""

    if not isinstance(value, Mapping) or set(value) != {
        "transport_payload",
        "canonical_answer",
        "normalization_receipt",
    }:
        raise ValueError("answer adaptation fields changed")
    transport = value.get("transport_payload")
    canonical = value.get("canonical_answer")
    receipt = value.get("normalization_receipt")
    if not isinstance(transport, Mapping) or not isinstance(canonical, Mapping):
        raise ValueError("answer adaptation payload is invalid")
    validated_transport = validate_answer(transport)
    validated_canonical = validate_answer(canonical)
    transport_bytes = _canonical_bytes(validated_transport)
    canonical_bytes = _canonical_bytes(validated_canonical)
    if (
        not isinstance(receipt, Mapping)
        or set(receipt)
        != {
            "schema_version",
            "contract_version",
            "adaptation",
            "lossless",
            "raw_transport_sha256",
            "canonical_transport_sha256",
            "canonical_answer_sha256",
        }
        or receipt.get("schema_version") != ADAPTATION_RECEIPT_VERSION
        or receipt.get("contract_version") != CONTRACT_VERSION
        or receipt.get("adaptation") != "identity"
        or receipt.get("lossless") is not True
        or _SHA256.fullmatch(str(receipt.get("raw_transport_sha256"))) is None
        or receipt.get("canonical_transport_sha256")
        != hashlib.sha256(transport_bytes).hexdigest()
        or receipt.get("canonical_answer_sha256")
        != hashlib.sha256(canonical_bytes).hexdigest()
        or transport_bytes != canonical_bytes
    ):
        raise ValueError("answer adaptation receipt is not lossless")
    return {
        "transport_payload": validated_transport,
        "canonical_answer": validated_canonical,
        "normalization_receipt": dict(receipt),
    }


def answer_status(value: Mapping[str, Any]) -> str:
    """Validate and return the categorical answer status."""

    return str(validate_answer(value)["status"])


def prompt_instructions() -> str:
    """Return the exact natural-language rendering of the v2 state contract."""

    return (
        'When the packet supports a response, set status="answered", provide a '
        "nonempty answer, cite at least one visible ResourceType/id, and set "
        "insufficiency_reason=null. When it does not, set "
        'status="insufficient", set answer=null, explain the missing evidence, '
        "and cite visible resources that establish the insufficiency when useful; "
        "citations may be empty only when no visible resource supports that "
        "explanation."
    )
