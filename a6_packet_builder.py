#!/usr/bin/env python3
"""Build A6 query-aware frozen packets for Codex/API answering arms.

A6 tests whether an in-context projection can match the sandbox by selecting the
right FHIR slice before the model reads it. This script deliberately excludes
gold answer fields and can run in `--plan-only` mode without a live Medplum
server, so the intent layer is inspectable before spend.
"""

from __future__ import annotations

import argparse
import ast
import csv
import datetime as dt
import hashlib
import json
import math
import re
import urllib.parse
from pathlib import Path
from typing import Any


GOLD_FIELDS = {"true_answer", "true_fhir_ids", "sql_query", "proc_query"}

TABLE_TO_RESOURCES = {
    "admissions": ["Encounter"],
    "chartevents": ["Observation"],
    "diagnoses_icd": ["Condition"],
    "icustays": ["Encounter"],
    "labevents": ["Observation"],
    "microbiologyevents": ["Observation"],
    "outputevents": ["Observation"],
    "patients": ["Patient"],
    "prescriptions": ["MedicationRequest"],
    "procedures_icd": ["Procedure"],
    "transfers": ["Encounter"],
}

RESOURCE_DATE_PARAM = {
    "Encounter": "date",
    "MedicationRequest": "authoredon",
    "Observation": "date",
    "Procedure": "date",
}

TEXT_KEYS = {
    "careunit",
    "drug_name",
    "drug_name1",
    "drug_name2",
    "drug_name3",
    "drug_route",
    "lab_name",
    "output_name",
    "procedure_name",
    "spec_name",
    "vital_name",
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_val_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, float) and math.isnan(raw):
        return {}
    if isinstance(raw, dict):
        return raw
    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return {}
    try:
        return ast.literal_eval(text)
    except Exception:
        try:
            return json.loads(text)
        except Exception:
            return {}


def current_date_from_assumption(text: Any) -> dt.date | None:
    match = re.search(r"current time is (\d{4})-(\d{2})-(\d{2})", str(text or ""))
    if not match:
        return None
    return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _month_end(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (dt.date(year, month + 1, 1) - dt.timedelta(days=1)).day


def parse_nlq_window(nlq: str, current_date: dt.date | None) -> dict[str, str] | None:
    n = (nlq or "").lower().strip()
    if not n:
        return None

    m = re.search(r"\b(?:in|since|on|during)\s+(\d{1,2})/(\d{4})\b", n)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if "since" in n:
            return {"start": f"{year:04d}-{month:02d}-01", "end": None, "source": nlq}
        return {
            "start": f"{year:04d}-{month:02d}-01",
            "end": f"{year:04d}-{month:02d}-{_month_end(year, month):02d}",
            "source": nlq,
        }

    m = re.search(r"\bon\s+(\d{1,2})/(\d{1,2})/(?:this year|the current year)\b", n)
    if m and current_date:
        month, day = int(m.group(1)), int(m.group(2))
        value = f"{current_date.year:04d}-{month:02d}-{day:02d}"
        return {"start": value, "end": value, "source": nlq}

    m = re.search(r"\b(?:in|during)\s+(\d{4})\b", n)
    if m:
        year = int(m.group(1))
        return {"start": f"{year:04d}-01-01", "end": f"{year:04d}-12-31", "source": nlq}

    m = re.search(r"\bsince\s+(\d{4})\b", n)
    if m:
        year = int(m.group(1))
        return {"start": f"{year:04d}-01-01", "end": None, "source": nlq}

    if current_date and ("last year" in n or "previous year" in n):
        year = current_date.year - 1
        return {"start": f"{year:04d}-01-01", "end": f"{year:04d}-12-31", "source": nlq}

    m = re.search(r"\bin\s+(\d{1,2})/last year\b", n)
    if m and current_date:
        month, year = int(m.group(1)), current_date.year - 1
        return {
            "start": f"{year:04d}-{month:02d}-01",
            "end": f"{year:04d}-{month:02d}-{_month_end(year, month):02d}",
            "source": nlq,
        }

    return None


def infer_resource_types(row: dict[str, Any]) -> list[str]:
    table = str(row.get("main_table_name") or "").strip()
    resources = TABLE_TO_RESOURCES.get(table, [])
    if resources:
        return resources
    q = str(row.get("question") or "").lower()
    if any(w in q for w in ("lab", "blood pressure", "heart rate", "weight", "height", "output", "microbiology")):
        return ["Observation"]
    if any(w in q for w in ("medication", "prescribed", "drug")):
        return ["MedicationRequest"]
    if "procedure" in q:
        return ["Procedure"]
    if any(w in q for w in ("admission", "discharge", "hospital", "icu", "careunit", "visit")):
        return ["Encounter"]
    return ["Patient"]


def _sorted_terms(val_dict: dict[str, Any]) -> list[str]:
    val = val_dict.get("val_placeholder") or {}
    terms = []
    for key in sorted(TEXT_KEYS):
        value = val.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and not text.isdigit():
            terms.append(text.lower())
    return sorted(set(terms))


def _date_windows(val_dict: dict[str, Any], current_date: dt.date | None) -> list[dict[str, Any]]:
    windows = []
    for item in (val_dict.get("time_placeholder") or {}).values():
        if not isinstance(item, dict):
            continue
        window = parse_nlq_window(str(item.get("nlq") or ""), current_date)
        if window and window not in windows:
            windows.append(window)
    return windows


def _temporal_policy(row: dict[str, Any], val_dict: dict[str, Any]) -> str:
    q = str(row.get("question") or "").lower()
    time_values = " ".join(str(v.get("nlq", "")) for v in (val_dict.get("time_placeholder") or {}).values() if isinstance(v, dict)).lower()
    combined = q + " " + time_values
    combined = re.sub(r"\b(?:last|previous)\s+(?:year|month|week)\b", "", combined)
    if any(word in combined for word in ("first", "earliest", "initial", "second", "last", "latest", "change in")):
        return "first_last"
    return "recent"


def infer_intent(row: dict[str, Any]) -> dict[str, Any]:
    val_dict = parse_val_dict(row.get("val_dict"))
    current_date = current_date_from_assumption(row.get("assumption"))
    return {
        "resource_types": infer_resource_types(row),
        "search_terms": _sorted_terms(val_dict),
        "date_windows": _date_windows(val_dict, current_date),
        "temporal_policy": _temporal_policy(row, val_dict),
        "current_date": current_date.isoformat() if current_date else None,
    }


def _patient_id(row: dict[str, Any]) -> str:
    value = str(row.get("patient_fhir_id") or "").strip()
    if value.startswith("Patient/"):
        return value.split("/", 1)[1]
    return value


def _add_date_params(parts: list[str], resource_type: str, window: dict[str, Any] | None) -> None:
    param = RESOURCE_DATE_PARAM.get(resource_type)
    if not param or not window:
        return
    if window.get("start"):
        parts.append(f"{param}=ge{window['start']}")
    if window.get("end"):
        parts.append(f"{param}=le{window['end']}")


def _observation_code_text(term: str) -> str | None:
    # Avoid using route/careunit words as Observation code text.
    if term in {"iv", "po", "sc", "im", "oral"}:
        return None
    return term


def _query_for(resource_type: str, row: dict[str, Any], intent: dict[str, Any], *, count: int, sort: str | None) -> str:
    patient_id = _patient_id(row)
    if resource_type == "Patient":
        return f"Patient?_id={urllib.parse.quote(patient_id)}&_count=1"

    parts = [f"patient={urllib.parse.quote(patient_id)}", f"_count={count}"]
    if sort:
        parts.append(f"_sort={urllib.parse.quote(sort)}")

    window = intent.get("date_windows", [None])[0] if intent.get("date_windows") else None
    _add_date_params(parts, resource_type, window)

    terms = intent.get("search_terms") or []
    if resource_type == "Observation" and terms:
        code_text = _observation_code_text(terms[0])
        if code_text:
            parts.append(f"code:text={urllib.parse.quote(code_text)}")
    if resource_type == "Procedure" and terms:
        parts.append(f"code:text={urllib.parse.quote(terms[0])}")
    if resource_type == "MedicationRequest":
        parts.append("_include=MedicationRequest:medication")
    if resource_type == "Encounter" and any(t for t in terms if "icu" in t):
        parts.append("class=IMP")

    return f"{resource_type}?" + "&".join(parts)


def build_search_plan(row: dict[str, Any], intent: dict[str, Any] | None = None, *, count: int = 100) -> list[dict[str, Any]]:
    intent = intent or infer_intent(row)
    plan = []
    for resource_type in intent["resource_types"]:
        if intent["temporal_policy"] == "first_last" and resource_type != "Patient":
            sorts = ["date", "-date"]
        else:
            sorts = ["-date"] if resource_type in RESOURCE_DATE_PARAM else [None]
        for sort in sorts:
            path = _query_for(resource_type, row, intent, count=count, sort=sort)
            item = {
                "resource_type": resource_type,
                "path": path,
                "reason": "query-aware selection from non-gold question metadata",
            }
            if item not in plan:
                plan.append(item)
    return plan


def _resource_id(resource: dict[str, Any]) -> str | None:
    rtype, rid = resource.get("resourceType"), resource.get("id")
    if rtype and rid:
        return f"{rtype}/{rid}"
    return None


def _dedupe_resources(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for resource in resources:
        rid = _resource_id(resource)
        key = rid or sha256_text(_json(resource))
        if key in seen:
            continue
        seen.add(key)
        out.append(resource)
    return out


def fetch_resources(plan: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    from fhir_client import get_fhir_client

    client = get_fhir_client()
    out = {}
    for item in plan:
        try:
            out[item["path"]] = client.search_with_pagination(item["path"])
        except Exception as exc:
            out[item["path"]] = [{"resourceType": "OperationOutcome", "issue": [{"diagnostics": str(exc)}]}]
    return out


def build_packet_record(
    row: dict[str, Any],
    *,
    plan_only: bool,
    resources_by_query: dict[str, list[dict[str, Any]]] | None,
    count: int = 100,
) -> dict[str, Any]:
    safe = {k: v for k, v in row.items() if k not in GOLD_FIELDS}
    intent = infer_intent(safe)
    plan = build_search_plan(safe, intent, count=count)
    resources_by_query = resources_by_query or {}
    resources = []
    for item in plan:
        resources.extend(resources_by_query.get(item["path"], []))
    resources = _dedupe_resources(resources)
    resource_ids = [rid for rid in (_resource_id(r) for r in resources) if rid]
    packet = {
        "kind": "a6_query_aware_packet",
        "plan_only": plan_only,
        "resources": [] if plan_only else resources,
        "resource_count": 0 if plan_only else len(resources),
        "source_resource_ids": [] if plan_only else sorted(resource_ids),
        "source_queries": plan,
    }
    packet["sha256"] = sha256_text(_json(packet))
    return {
        "question_id": safe.get("question_id"),
        "question": safe.get("question"),
        "patient_fhir_id": safe.get("patient_fhir_id"),
        "assumption": safe.get("assumption"),
        "intent": intent,
        "packet": packet,
    }


def load_rows(input_path: Path, *, limit: int | None = None, split: str | None = "test") -> list[dict[str, Any]]:
    with input_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if split:
        rows = [r for r in rows if r.get("split") == split]
    if limit is not None:
        rows = rows[:limit]
    return rows


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_manifest(path: Path, *, input_path: Path, output_path: Path, args: argparse.Namespace, records: list[dict[str, Any]]) -> None:
    manifest = {
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "kind": "a6_query_aware_packet_manifest",
        "input": {"path": str(input_path), "sha256": sha256_file(input_path)},
        "output": {"path": str(output_path), "sha256": sha256_file(output_path)},
        "config": {
            "limit": args.limit,
            "count": args.count,
            "plan_only": args.plan_only,
            "split": args.split,
        },
        "questions": len(records),
        "packet_hashes": {str(r["question_id"]): r["packet"]["sha256"] for r in records},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build A6 query-aware frozen packets.")
    parser.add_argument("--input", type=Path, default=Path("final_dataset/full_test409.csv"))
    parser.add_argument("--output", type=Path, default=Path("runs/a6_query_aware_packets.jsonl"))
    parser.add_argument("--manifest", type=Path, default=Path("runs/a6_query_aware_manifest.json"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--split", default="test")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    rows = load_rows(args.input, limit=args.limit, split=args.split)
    records = []
    for row in rows:
        plan = build_search_plan(row, count=args.count)
        resources = {} if args.plan_only else fetch_resources(plan)
        records.append(build_packet_record(row, plan_only=args.plan_only, resources_by_query=resources, count=args.count))
    write_jsonl(args.output, records)
    write_manifest(args.manifest, input_path=args.input, output_path=args.output, args=args, records=records)
    print(json.dumps({"output": str(args.output), "manifest": str(args.manifest), "records": len(records), "plan_only": args.plan_only}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
