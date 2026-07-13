"""Explicit question-set selection for grading and result assembly."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, TypeVar


Row = TypeVar("Row")


def _ids_from_json(data: Any, *, source: Path) -> list[str]:
    if isinstance(data, dict):
        data = data.get("question_ids")
    if not isinstance(data, list):
        raise ValueError(f"question spec must be a JSON list or an object with question_ids: {source}")
    ids = [str(value).strip() for value in data]
    if not ids or any(not value for value in ids):
        raise ValueError(f"question spec contains no usable question IDs: {source}")
    return ids


def load_scheduled_question_ids(
    *,
    spec_path: Path | None = None,
    repeated_ids: Iterable[str] = (),
) -> list[str] | None:
    """Load an explicit schedule, preserving first-seen order.

    ``None`` means no schedule was supplied and callers should retain their
    historical full-input behavior. A JSON spec may be either ``["q1"]`` or
    ``{"question_ids": ["q1"]}``; repeated CLI IDs can be used alone or added
    to that list.
    """
    values: list[str] = []
    if spec_path is not None:
        values.extend(_ids_from_json(json.loads(spec_path.read_text(encoding="utf-8")), source=spec_path))
    values.extend(str(value).strip() for value in repeated_ids)
    if not values:
        return None
    if any(not value for value in values):
        raise ValueError("scheduled question IDs must be non-empty strings")
    return list(dict.fromkeys(values))


def select_question_rows(rows: Mapping[str, Row], scheduled_ids: list[str] | None) -> dict[str, Row]:
    """Restrict keyed rows to an explicit schedule and reject unknown IDs."""
    if scheduled_ids is None:
        return dict(rows)
    missing = [question_id for question_id in scheduled_ids if question_id not in rows]
    if missing:
        preview = ", ".join(missing[:10])
        suffix = "..." if len(missing) > 10 else ""
        raise ValueError(f"scheduled question IDs missing from input ({len(missing)}): {preview}{suffix}")
    return {question_id: rows[question_id] for question_id in scheduled_ids}

