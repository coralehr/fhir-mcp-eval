#!/usr/bin/env python3
"""Replace one frozen question stratum in a packet JSONL without model calls."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict) or not record.get("question_id"):
            raise ValueError(f"{path}:{line_number} has no question_id")
        question_id = str(record["question_id"])
        if question_id in seen:
            raise ValueError(f"duplicate question_id {question_id} in {path}")
        seen.add(question_id)
        records.append(record)
    return records


def load_spec(path: Path) -> list[str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("question_ids"), list):
        raise ValueError("spec must contain question_ids")
    ids = [str(item) for item in value["question_ids"]]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("spec question_ids must be non-empty and unique")
    return ids


def replace_records(
    base: list[dict[str, Any]],
    updates: list[dict[str, Any]],
    question_ids: list[str],
    *,
    expected_total: int,
    expected_updates: int,
) -> list[dict[str, Any]]:
    if len(base) != expected_total:
        raise ValueError(f"base has {len(base)} records, expected {expected_total}")
    if len(question_ids) != expected_updates:
        raise ValueError(
            f"spec has {len(question_ids)} update IDs, expected {expected_updates}"
        )
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("frozen question spec IDs must be unique")
    base_ids = [str(record["question_id"]) for record in base]
    if len(base_ids) != len(set(base_ids)):
        raise ValueError("base contains duplicate question IDs")
    update_map = {str(record["question_id"]): record for record in updates}
    if len(update_map) != len(updates):
        raise ValueError("updates contain duplicate question IDs")
    if set(update_map) != set(question_ids):
        raise ValueError("updates do not exactly match the frozen question spec")
    missing = set(question_ids) - set(base_ids)
    if missing:
        raise ValueError("frozen question spec is not a subset of the base packet")

    result: list[dict[str, Any]] = []
    for question_id, base_record in zip(base_ids, base):
        update = update_map.get(question_id)
        if update is None:
            result.append(base_record)
            continue
        if not isinstance(update.get("packet"), dict):
            raise ValueError(f"update {question_id} has no packet object")

        # A stratum rebuild is permitted to replace clinical packet evidence,
        # not the frozen benchmark row that the harness overlays it onto.
        # Copy every top-level value from the base and replace only `packet`.
        result.append({**base_record, "packet": update["packet"]})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--updates", type=Path, required=True)
    parser.add_argument("--question-spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-total", type=int, required=True)
    parser.add_argument("--expected-updates", type=int, required=True)
    args = parser.parse_args()

    records = replace_records(
        load_records(args.base),
        load_records(args.updates),
        load_spec(args.question_spec),
        expected_total=args.expected_total,
        expected_updates=args.expected_updates,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    manifest = {
        "kind": "packet_stratum_replacement",
        "inputs": {
            "base": {"path": str(args.base), "sha256": sha256_file(args.base)},
            "updates": {
                "path": str(args.updates),
                "sha256": sha256_file(args.updates),
            },
            "question_spec": {
                "path": str(args.question_spec),
                "sha256": sha256_file(args.question_spec),
            },
        },
        "expected_total": args.expected_total,
        "expected_updates": args.expected_updates,
        "output": {"path": str(args.output), "sha256": sha256_file(args.output)},
    }
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
