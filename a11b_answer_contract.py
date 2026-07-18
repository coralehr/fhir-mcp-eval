"""Versioned answer-state contract for A11b successor experiments.

The historical r3 preview used a prose sentinel to distinguish substantive
answers from insufficiency.  This module makes that state categorical so the
prompt, schema, grader, and behavior metrics cannot infer different meanings
from answer wording.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


CONTRACT_VERSION = "a11b-answer-contract-v2"
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


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_answer(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return one validated answer or fail closed on ambiguous state."""

    if not isinstance(value, Mapping) or set(value) != FIELDS:
        raise ValueError("answer fields do not match the v2 contract")
    status = value.get("status")
    if status not in {ANSWERED, INSUFFICIENT}:
        raise ValueError("answer status must be answered or insufficient")
    evidence_summary = value.get("evidence_summary")
    if not _nonempty(evidence_summary):
        raise ValueError("evidence_summary must contain non-whitespace text")
    sources = value.get("source_resource_ids")
    if (
        not isinstance(sources, list)
        or any(
            not isinstance(source, str)
            or _FHIR_REFERENCE.fullmatch(source) is None
            for source in sources
        )
        or len(sources) != len(set(sources))
    ):
        raise ValueError("source_resource_ids must be unique FHIR references")

    answer = value.get("answer")
    reason = value.get("insufficiency_reason")
    if status == ANSWERED:
        if not _nonempty(answer) or not sources or reason is not None:
            raise ValueError(
                "answered state requires an answer, citations, and null reason"
            )
    elif answer is not None or not _nonempty(reason):
        raise ValueError(
            "insufficient state requires null answer and a nonempty reason"
        )
    return dict(value)


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
