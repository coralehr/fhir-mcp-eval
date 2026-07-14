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
from typing import Any, Callable


GOLD_FIELDS = {"true_answer", "true_fhir_ids", "sql_query", "proc_query"}

# A6a (question-only) planner: the ONLY row fields the planner may read.
# Everything else in the CSV (main_table_name, val_dict, template, ...) is
# benchmark-construction metadata that does not exist for a real user query
# (adversarial review 2026-07-11, finding 1). Whitelist, not blacklist.
QUESTION_ONLY_FIELDS = {"split", "question_id", "question", "assumption", "patient_fhir_id"}


class PacketFetchError(RuntimeError):
    """A redacted transport/incompleteness failure that invalidates a packet."""

# Patch-level common-planner revision frozen for the untouched QT-4 holdout.
# It preserves qo-v2 bounds and feature isolation while applying the structural
# routing/query-validity repair pre-built from the earlier 409-question audit.
QO_PLANNER_VERSION = "qo-v2.1"
A6A_MAX_TOTAL_RESOURCES = 200
A6A_MAX_PACKET_CHARS = 160_000

# Frozen QT-4V vocabulary. These tokens match the MIMIC-on-FHIR microbiology
# parent Observation code displays (for example URINE CULTURE and MRSA SCREEN)
# without reading benchmark-construction metadata.
MICRO_VOCABULARY_VERSION = "micro-v1"
MICRO_DISPATCHER_VERSION = "micro-dispatch-v1"
MICRO_CODE_TEXT_TERMS = ("culture", "gram stain", "screen", "smear")
MICRO_QUESTION_TERMS = (
    "microbiolog",
    "microbial",
    "culture",
    "specimen",
    "organism",
    "smear",
    "gram stain",
    "screen",
)

MICRO_TRAVERSAL_VERSION = "micro-traversal-v1"
MICRO_TRAVERSAL_MAX_DEPTH = 2
MICRO_TRAVERSAL_MAX_RESOURCES = 24
MICRO_TRAVERSAL_MAX_SERIALIZED_BYTES = 24_000
MICRO_TRAVERSAL_MAX_PATH_RECEIPTS = 48
MICRO_TRAVERSAL_MAX_PATH_RECEIPT_BYTES = 12_000
MICRO_TRAVERSAL_PATH_STATUSES = (
    "fetched",
    "already_present",
    "missing",
    "max_resources",
    "max_serialized_bytes",
)
FHIR_ID_PATTERN = re.compile(r"[A-Za-z0-9\-.]{1,64}")

# Explicit, forward FHIR references only. No reverse search, inferred edges, or
# generic recursive walk is allowed in the QT-4T treatment.
MICRO_REFERENCE_PATHS: dict[str, tuple[tuple[str, str], ...]] = {
    "DiagnosticReport": (("result", "Observation"), ("specimen", "Specimen")),
    "Observation": (("hasMember", "Observation"), ("specimen", "Specimen")),
}

QT_FEATURES = (
    "include-pinning",
    "endpoint-reserve",
    "agg-summary",
    "micro-vocab",
    "micro-traversal",
)
REGISTERED_QT_ARMS = (
    frozenset(),
    frozenset({"include-pinning"}),
    frozenset({"endpoint-reserve"}),
    frozenset({"agg-summary"}),
    frozenset({"micro-vocab"}),
    frozenset({"micro-vocab", "micro-traversal"}),
)

# Product-facing evidence recipe promoted by the sealed QT-4 valid374 holdout.
# Historical experiment entrypoints still default to explicit feature sets so
# old manifests remain reproducible. ``compile_evidence.py`` opts into this
# recipe by default for new packet builds.
PROMOTED_EVIDENCE_RECIPE = "qt4-vocabulary-promoted-v1"
EVIDENCE_RECIPES = (PROMOTED_EVIDENCE_RECIPE,)


def validate_qt_features(
    features: set[str] | frozenset[str], *, planner: str
) -> frozenset[str]:
    normalized = frozenset(features)
    unknown = normalized - set(QT_FEATURES)
    if unknown:
        raise ValueError(f"unknown features: {sorted(unknown)}; valid: {QT_FEATURES}")
    if normalized not in REGISTERED_QT_ARMS:
        raise ValueError(
            f"feature set {sorted(normalized)} is not a registered QT arm; "
            "run one single-feature arm or QT-4V/QT-4T"
        )
    if normalized and planner != "question-only":
        raise ValueError(
            f"QT features require the question-only {QO_PLANNER_VERSION} planner"
        )
    return normalized


def resolve_evidence_recipe(
    recipe: str | None,
    *,
    explicit_features: set[str] | frozenset[str],
    planner: str,
) -> frozenset[str]:
    """Resolve a versioned product recipe without changing historical arms.

    A recipe and an explicit experiment feature set are mutually exclusive:
    the former is a promoted product configuration, while the latter keeps the
    older single-treatment experiment interface reproducible.
    """

    if recipe is None:
        return validate_qt_features(explicit_features, planner=planner)
    if recipe not in EVIDENCE_RECIPES:
        raise ValueError(
            f"unknown evidence recipe: {recipe}; valid: {EVIDENCE_RECIPES}"
        )
    if explicit_features:
        raise ValueError(
            "--evidence-recipe and --features are mutually exclusive"
        )
    if recipe == PROMOTED_EVIDENCE_RECIPE:
        return validate_qt_features({"micro-vocab"}, planner=planner)
    raise AssertionError(f"unhandled evidence recipe: {recipe}")


def resolve_a6a_root_bounds(
    *,
    planner: str,
    max_total_resources: int | None,
    max_packet_chars: int | None,
) -> tuple[int | None, int | None]:
    if planner != "question-only":
        return max_total_resources, max_packet_chars
    resolved_resources = (
        A6A_MAX_TOTAL_RESOURCES if max_total_resources is None else max_total_resources
    )
    resolved_chars = A6A_MAX_PACKET_CHARS if max_packet_chars is None else max_packet_chars
    if (
        resolved_resources != A6A_MAX_TOTAL_RESOURCES
        or resolved_chars != A6A_MAX_PACKET_CHARS
    ):
        raise ValueError(
            "question-only packet builds require frozen A6a root bounds "
            f"{A6A_MAX_TOTAL_RESOURCES} resources / {A6A_MAX_PACKET_CHARS} chars"
        )
    return resolved_resources, resolved_chars


def is_microbiology_question(question: Any) -> bool:
    return any(term in str(question or "").lower() for term in MICRO_QUESTION_TERMS)

# Deterministic question-text -> resource-type mapping. Order matters only for
# reporting; multiple groups may fire and are unioned.
QO_TYPE_KEYWORDS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("prescri", "medication", "drug", "dose", "tablet", "capsule", " mg ", "infusion"), ("MedicationRequest",)),
    (
        (
            "procedure", "surgery", "surgical", "operation", "ventilation", "intubat",
            "dialysis", "catheter", "undergo", "underwent", "insert", "irrigat",
            "destruct", "restrict", "resect", "excis", "drainage", "introduc",
            "injection or infusion",
        ),
        ("Procedure",),
    ),
    (("diagnos", "condition", "disease", "disorder"), ("Condition",)),
    (
        ("admit", "admission", "discharge", "hospital", "icu", "intensive care", "careunit", "care unit", "ward", "transfer", "visit", "encounter", "stay"),
        ("Encounter",),
    ),
    (
        (
            "lab", "test", "level", "value", "measure", "microbiolog", "culture", "specimen", "organism",
            "weight", "height", "temperature", "heart rate", "blood pressure", "respiratory", "oxygen", "o2",
            "sao2", "spo2", "glucose", "sodium", "potassium", "creatinine", "hemoglobin", "hematocrit",
            "platelet", "urine", "output", "input", "intake", "drain", "stool", "emesis",
            "cerebral ventricular", "immunoglobulin", "immune globulin",
        ),
        ("Observation",),
    ),
    (("gender", "sex", "age", "born", "birth", "male", "female", "race", "ethnic", "marital", "language"), ("Patient",)),
]

# Fallback when no keyword group fires: the two types that dominate the gold
# distribution. Bounds (not breadth) keep this safe.
QO_FALLBACK_TYPES = ("Observation", "Encounter")

# Question-scaffold vocabulary stripped before clinical-term extraction.
QO_SCAFFOLD_WORDS = {
    "a", "an", "and", "any", "been", "calculate", "compared", "count", "current", "date", "day", "days",
    "did", "do", "does", "during", "first", "for", "from", "get", "give", "given", "had", "has", "have",
    "his", "her", "how", "in", "is", "it", "last", "many", "me", "month", "months", "much", "number",
    "of", "on", "or", "patient", "receive", "received", "same", "second", "show", "since", "tell",
    "than", "the", "their", "there", "this", "time", "times", "to", "today", "total", "until", "was",
    "were", "what", "when", "where", "which", "who", "whose", "with", "year", "years", "yesterday",
    "change", "difference", "list", "all", "ever", "earliest", "latest", "previous", "prescribed",
    "prescription", "prescriptions", "medication", "medications", "drug", "drugs", "procedure",
    "procedures", "lab", "labs", "value", "values", "measured", "measurement", "measurements",
    "hospital", "encounter", "visit", "admitted", "admission", "care", "unit", "route", "via",
    "can", "could", "you", "please", "show", "compute", "duration", "minimum", "maximum",
    "min", "max", "average", "mean", "take", "took", "taken",
}

# Top-level keys stripped from every resource before it enters a bounded
# packet (mirrors the A0' strip set, adding `contained`).
PROJECTION_DROP_KEYS = ("text", "meta", "extension", "modifierExtension", "contained")

# A0' (blunt query-blind projection) as a frozen packet: every gold resource
# type, no question-derived filters, per-type recency cap. The contemporaneous
# control arm required by the A6a pre-registration (review finding 6).
# Medication and Location have no `patient` search param; they enter packets
# via _include on MedicationRequest and Encounter respectively.
BLUNT_RESOURCE_TYPES = (
    "Patient",
    "Encounter",
    "Observation",
    "MedicationRequest",
    "Procedure",
    "Condition",
)
BLUNT_PER_TYPE_CAP = 50

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

    m = re.search(r"\b(?:in|since|during)\s+(\d{1,2})/(?:this year|the current year)\b", n)
    if m and current_date:
        month, year = int(m.group(1)), current_date.year
        if "since" in n:
            return {"start": f"{year:04d}-{month:02d}-01", "end": None, "source": nlq}
        return {
            "start": f"{year:04d}-{month:02d}-01",
            "end": f"{year:04d}-{month:02d}-{_month_end(year, month):02d}",
            "source": nlq,
        }

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
        "planner": "metadata-oracle",
        "resource_types": infer_resource_types(row),
        "search_terms": _sorted_terms(val_dict),
        "date_windows": _date_windows(val_dict, current_date),
        "temporal_policy": _temporal_policy(row, val_dict),
        "current_date": current_date.isoformat() if current_date else None,
    }


# ---------------------------------------------------------------------------
# A6a question-only planner: reads QUESTION_ONLY_FIELDS and nothing else.
# ---------------------------------------------------------------------------


def _qo_keyword_matches(question: str, keyword: str) -> bool:
    """Match a word/phrase or stem at a leading token boundary."""
    normalized = keyword.strip().lower()
    if not normalized:
        return False
    if normalized == "drug":
        return (
            re.search(r"(?<!non-)(?<![a-z0-9])drug(?!-elut)", question)
            is not None
        )
    return re.search(r"(?<![a-z0-9])" + re.escape(normalized), question) is not None


def qo_infer_resource_types(question: str) -> list[str]:
    q = str(question or "").lower()
    medication_match = any(
        _qo_keyword_matches(q, keyword) for keyword in QO_TYPE_KEYWORDS[0][0]
    )
    strong_medication_match = any(
        _qo_keyword_matches(q, keyword)
        for keyword in QO_TYPE_KEYWORDS[0][0]
        if keyword.strip() != "infusion"
    )
    procedure_code_phrase = any(
        _qo_keyword_matches(q, keyword) for keyword in QO_TYPE_KEYWORDS[1][0]
    )
    explicit_procedure_frame = any(
        _qo_keyword_matches(q, keyword)
        for keyword in (
            "procedure", "surgery", "surgical", "operation", "ventilation",
            "intubat", "dialysis", "undergo", "underwent",
        )
    )
    types: list[str] = []
    for keywords, resources in QO_TYPE_KEYWORDS:
        if any(_qo_keyword_matches(q, keyword) for keyword in keywords):
            for r in resources:
                if (
                    r == "Procedure"
                    and strong_medication_match
                    and not explicit_procedure_frame
                ):
                    continue
                if (
                    r == "MedicationRequest"
                    and medication_match
                    and not strong_medication_match
                    and procedure_code_phrase
                ):
                    continue
                if r not in types:
                    types.append(r)
    return types or list(QO_FALLBACK_TYPES)


def qo_extract_terms(question: str) -> list[str]:
    """Longest contiguous runs of non-scaffold tokens, as candidate clinical terms.

    Deterministic and deliberately dumb: template questions embed the clinical
    entity as a contiguous span ("... last <heart rate> value of patient ...").
    """
    q = str(question or "").lower()
    q = re.sub(r"\bpatient\s+\S+", " ", q)  # drop "patient 10014729"
    q = re.sub(r"\b\d{1,2}/\d{4}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}\b|\b\d{4}\b", " ", q)
    tokens = [t for t in re.findall(r"[a-z0-9][a-z0-9%.\-]*", q)]
    runs: list[list[str]] = []
    current: list[str] = []
    for i, tok in enumerate(tokens):
        # "of" is a connector inside clinical names ("milk of magnesia"),
        # not a run breaker, when flanked by non-scaffold tokens.
        if tok == "of" and current and i + 1 < len(tokens) and tokens[i + 1] not in QO_SCAFFOLD_WORDS and not tokens[i + 1].isdigit():
            current.append(tok)
            continue
        if tok in QO_SCAFFOLD_WORDS or tok.isdigit():
            if current:
                runs.append(current)
                current = []
            continue
        current.append(tok)
    if current:
        runs.append(current)
    terms = []
    for run in sorted(runs, key=len, reverse=True):
        term = " ".join(run).strip(" .-")
        if len(term) >= 3 and term not in terms:
            terms.append(term)
        if len(terms) >= 3:
            break
    return terms


def qo_temporal_policy(question: str) -> str:
    q = str(question or "").lower()
    q = re.sub(r"\b(?:last|previous)\s+(?:year|month|week)\b", "", q)
    if any(word in q for word in ("first", "earliest", "initial", "second", "last", "latest", "change in")):
        return "first_last"
    return "recent"


def blunt_infer_intent(row: dict[str, Any]) -> dict[str, Any]:
    """A0' intent: query-blind — all gold types, no terms, no windows."""
    qrow = {k: row.get(k) for k in QUESTION_ONLY_FIELDS}
    current_date = current_date_from_assumption(qrow.get("assumption"))
    return {
        "planner": "blunt-projection-v1",
        "resource_types": list(BLUNT_RESOURCE_TYPES),
        "search_terms": [],
        "date_windows": [],
        "temporal_policy": "recent",
        "current_date": current_date.isoformat() if current_date else None,
    }


def qo_infer_intent(row: dict[str, Any]) -> dict[str, Any]:
    qrow = {k: row.get(k) for k in QUESTION_ONLY_FIELDS}
    question = str(qrow.get("question") or "")
    current_date = current_date_from_assumption(qrow.get("assumption"))
    window = parse_nlq_window(question, current_date)
    return {
        "planner": QO_PLANNER_VERSION,
        "resource_types": qo_infer_resource_types(question),
        "search_terms": qo_extract_terms(question),
        "date_windows": [window] if window else [],
        "temporal_policy": qo_temporal_policy(question),
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
    if resource_type == "Encounter":
        # Location has no patient search param; ward/careunit evidence rides in here.
        parts.append("_include=Encounter:location")
    # NOTE: no Encounter class filter. Measured on the MIMIC-IV-on-FHIR demo
    # (2026-07-11 dev pilot): encounters are not coded class=IMP, so the old
    # `class=IMP` ICU heuristic zeroed out every ICU-phrased Encounter query.

    return f"{resource_type}?" + "&".join(parts)


def build_search_plan(
    row: dict[str, Any],
    intent: dict[str, Any] | None = None,
    *,
    count: int = 100,
    features: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    intent = intent or infer_intent(row)
    planner = "question-only" if intent.get("planner") == QO_PLANNER_VERSION else str(intent.get("planner"))
    features = validate_qt_features(features, planner=planner)
    plan = []
    micro_vocab = "micro-vocab" in features and is_microbiology_question(row.get("question"))
    for resource_type in intent["resource_types"]:
        if intent["temporal_policy"] == "first_last" and resource_type in RESOURCE_DATE_PARAM:
            sorts = ["date", "-date"]
        else:
            sorts = ["-date"] if resource_type in RESOURCE_DATE_PARAM else [None]
        terms: tuple[str | None, ...] = (
            MICRO_CODE_TEXT_TERMS if micro_vocab and resource_type == "Observation" else (None,)
        )
        for term in terms:
            query_intent = intent
            if term is not None:
                query_intent = {**intent, "search_terms": [term]}
            for sort in sorts:
                path = _query_for(resource_type, row, query_intent, count=count, sort=sort)
                item = {
                    "resource_type": resource_type,
                    "path": path,
                    "reason": (
                        f"fixed microbiology display vocabulary ({MICRO_VOCABULARY_VERSION})"
                        if term is not None
                        else (
                            "question-only selection (whitelisted fields: question, patient, assumption)"
                            if intent.get("planner") == QO_PLANNER_VERSION
                            else "query-aware selection from benchmark-construction metadata (oracle ceiling)"
                        )
                    ),
                }
                if term is not None:
                    # The four-query union is the complete QT-4V vocabulary
                    # treatment. Relaxing each miss to a bare Observation
                    # search would reintroduce the lab-volume failure it is
                    # designed to isolate.
                    item["relaxation_policy"] = "none"
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


def _allowed_reference_edges(resource: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return deterministic (source, JSON path, target) exact-reference edges."""
    source = _resource_id(resource)
    if source is None:
        return []
    source_type = str(resource.get("resourceType") or "")
    edges = []
    for field, target_type in MICRO_REFERENCE_PATHS.get(source_type, ()):
        raw = resource.get(field)
        values = raw if isinstance(raw, list) else [raw]
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                continue
            reference = value.get("reference")
            if not isinstance(reference, str):
                continue
            match = re.fullmatch(r"([A-Za-z][A-Za-z0-9]*)/([^/?#]+)", reference)
            if (
                match is None
                or match.group(1) != target_type
                or FHIR_ID_PATTERN.fullmatch(match.group(2)) is None
            ):
                continue
            suffix = f"[{index}]" if isinstance(raw, list) else ""
            edges.append((source, f"{source_type}.{field}{suffix}.reference", reference))
    return sorted(edges)


def traverse_exact_references(
    roots: list[dict[str, Any]],
    *,
    fetch_by_ids: Callable[[str, list[str]], list[dict[str, Any]]],
    max_depth: int = MICRO_TRAVERSAL_MAX_DEPTH,
    max_resources: int = MICRO_TRAVERSAL_MAX_RESOURCES,
    max_serialized_bytes: int = MICRO_TRAVERSAL_MAX_SERIALIZED_BYTES,
    max_path_receipts: int = MICRO_TRAVERSAL_MAX_PATH_RECEIPTS,
    max_path_receipt_bytes: int = MICRO_TRAVERSAL_MAX_PATH_RECEIPT_BYTES,
) -> dict[str, Any]:
    """Follow the frozen QT-4T reference allowlist with hard deterministic bounds.

    `fetch_by_ids` is the fetch-by-id seam used by both the real FHIR client and
    zero-model tests. Bounds cover unique target fetch attempts and serialized
    bytes added to the packet; root resources are governed by the base A6a cap.
    """
    if any(
        bound < 0
        for bound in (
            max_depth,
            max_resources,
            max_serialized_bytes,
            max_path_receipts,
            max_path_receipt_bytes,
        )
    ):
        raise ValueError("traversal bounds must be non-negative")
    if max_path_receipt_bytes < len(_json([]).encode("utf-8")):
        raise ValueError("max_path_receipt_bytes must be at least 2 for the empty JSON array")

    roots_by_id = {
        rid: resource
        for resource in roots
        if (rid := _resource_id(resource)) is not None
    }
    seen = set(roots_by_id)
    requested: set[str] = set()
    added: dict[str, dict[str, Any]] = {}
    receipts: list[dict[str, Any]] = []
    receipts_omitted = 0
    status_counts = {status: 0 for status in MICRO_TRAVERSAL_PATH_STATUSES}
    serialized_bytes = 0
    frontier = [roots_by_id[rid] for rid in sorted(roots_by_id)]

    for depth in range(1, max_depth + 1):
        edges = sorted(edge for resource in frontier for edge in _allowed_reference_edges(resource))
        if not edges:
            break

        fetchable = []
        for _, _, target in edges:
            if target in seen or target in requested or target in fetchable:
                continue
            if len(requested) + len(fetchable) >= max_resources:
                continue
            fetchable.append(target)

        fetched: dict[str, dict[str, Any]] = {}
        by_type: dict[str, list[str]] = {}
        for target in fetchable:
            resource_type, resource_id = target.split("/", 1)
            by_type.setdefault(resource_type, []).append(resource_id)
        for resource_type in sorted(by_type):
            ids = sorted(by_type[resource_type])
            requested.update(f"{resource_type}/{resource_id}" for resource_id in ids)
            for resource in fetch_by_ids(resource_type, ids):
                rid = _resource_id(resource)
                if rid in requested and rid not in fetched:
                    fetched[rid] = project_resource(resource)

        next_frontier: dict[str, dict[str, Any]] = {}
        for source, path, target in edges:
            if target in seen:
                status = "already_present"
            elif target not in requested:
                status = "max_resources"
            elif target not in fetched:
                status = "missing"
            else:
                candidate = fetched[target]
                candidate_bytes = len(_json(candidate).encode("utf-8"))
                if serialized_bytes + candidate_bytes > max_serialized_bytes:
                    status = "max_serialized_bytes"
                else:
                    status = "fetched"
                    seen.add(target)
                    added[target] = candidate
                    next_frontier[target] = candidate
                    serialized_bytes += candidate_bytes
            status_counts[status] += 1
            receipt = {
                "depth": depth,
                "from": source,
                "path": path,
                "to": target,
                "status": status,
            }
            candidate_receipts = receipts + [receipt]
            if (
                len(candidate_receipts) > max_path_receipts
                or len(_json(candidate_receipts).encode("utf-8")) > max_path_receipt_bytes
            ):
                receipts_omitted += 1
            else:
                receipts.append(receipt)
        frontier = [next_frontier[rid] for rid in sorted(next_frontier)]
        if not frontier:
            break

    return {
        "kind": "bounded_exact_reference_traversal",
        "version": MICRO_TRAVERSAL_VERSION,
        "limits": {
            "max_depth": max_depth,
            "max_resources": max_resources,
            "max_serialized_bytes": max_serialized_bytes,
            "max_path_receipts": max_path_receipts,
            "max_path_receipt_bytes": max_path_receipt_bytes,
        },
        "stats": {
            "fetch_attempt_count": len(requested),
            "added_resource_count": len(added),
            "added_serialized_bytes": serialized_bytes,
            "path_receipt_count": len(receipts),
            "path_receipt_serialized_bytes": len(_json(receipts).encode("utf-8")),
            "path_receipts_omitted": receipts_omitted,
            "path_status_counts": status_counts,
        },
        "resources": [added[rid] for rid in sorted(added)],
        "path_receipts": receipts,
    }


def _resource_clinical_date(resource: dict[str, Any]) -> str:
    """Best-effort clinical timestamp for ordering; empty string sorts first."""
    for key in ("effectiveDateTime", "authoredOn", "performedDateTime", "occurrenceDateTime", "recordedDate", "issued", "date"):
        value = resource.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("effectivePeriod", "period", "performedPeriod"):
        period = resource.get(key)
        if isinstance(period, dict) and isinstance(period.get("start"), str):
            return period["start"]
    return ""


def project_resource(resource: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in resource.items() if k not in PROJECTION_DROP_KEYS}


def blunt_bound(resources: list[dict[str, Any]], *, per_type_cap: int = BLUNT_PER_TYPE_CAP) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A0' packet discipline: strip fields, keep the `per_type_cap` most recent
    of each resource type. No global char cap — that is the historical A0'
    definition, kept faithful; overflow risk is A0''s own measured property."""
    projected = [project_resource(r) for r in resources]
    by_type: dict[str, list[dict[str, Any]]] = {}
    for r in projected:
        by_type.setdefault(str(r.get("resourceType") or "Unknown"), []).append(r)
    selected: list[dict[str, Any]] = []
    for rtype in sorted(by_type):
        items = sorted(by_type[rtype], key=_resource_clinical_date)
        selected.extend(items[-per_type_cap:])
    stats = {
        "input_count": len(resources),
        "kept_count": len(selected),
        "dropped_count": len(resources) - len(selected),
        "char_count": sum(len(_json(r)) for r in selected),
        "char_budget_hit": False,
        "per_type_cap": per_type_cap,
        "temporal_policy": "recent",
    }
    return selected, stats


# Reference targets that ride _include and must never be independently evicted
# (adversarial review 2026-07-12, single-feature arm 1).
PINNABLE_TARGET_TYPES = ("Medication/", "Location/")


def _iter_references(value):
    """Yield every FHIR reference string reachable in a resource dict."""
    if isinstance(value, dict):
        ref = value.get("reference")
        if isinstance(ref, str):
            yield ref
        for v in value.values():
            yield from _iter_references(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_references(v)


def pin_reference_targets(
    kept: list[dict[str, Any]], universe: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """Add Medication/Location resources referenced by kept resources back into
    the packet if they were fetched but evicted. Exempt from caps by design:
    a reference target is part of its referrer, not an independent candidate."""
    by_id = {}
    for r in universe:
        rid = _resource_id(r)
        if rid:
            by_id[rid] = r
    kept_ids = {rid for rid in (_resource_id(r) for r in kept) if rid}
    added = []
    for r in kept:
        for ref in _iter_references(r):
            if ref.startswith(PINNABLE_TARGET_TYPES) and ref not in kept_ids and ref in by_id:
                added.append(project_resource(by_id[ref]))
                kept_ids.add(ref)
    return kept + added, len(added)


def _quantity(resource: dict[str, Any]) -> tuple[float, str] | None:
    q = resource.get("valueQuantity")
    if isinstance(q, dict) and isinstance(q.get("value"), (int, float)):
        return float(q["value"]), str(q.get("unit") or q.get("code") or "")
    return None


def _med_display(resource: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str:
    for key in ("medicationCodeableConcept",):
        cc = resource.get(key)
        if isinstance(cc, dict):
            text = cc.get("text") or ""
            if text:
                return str(text)
    ref = (resource.get("medicationReference") or {}).get("reference")
    if isinstance(ref, str) and ref in by_id:
        code = by_id[ref].get("code") or {}
        return str(code.get("text") or "")
    return ""


def aggregate_summary(resources: list[dict[str, Any]], *, max_chars: int = 8_000) -> dict[str, Any]:
    """Question-blind deterministic reducer over the FULL fetched set, computed
    BEFORE bounding — so counts/sums/extremes survive even when the raw rows
    do not (single-feature arm 2). Semantics are explicit and versioned:
    counts are RESOURCE counts per (type, code text); medication_distinct is
    distinct display strings among MedicationRequests; sums are emitted only
    when every contributing value shares one unit."""
    by_id = {}
    for r in resources:
        rid = _resource_id(r)
        if rid:
            by_id[rid] = r
    per_type: dict[str, int] = {}
    series: dict[tuple[str, str], dict[str, Any]] = {}
    med_displays: dict[str, int] = {}
    for r in resources:
        rtype = str(r.get("resourceType") or "Unknown")
        per_type[rtype] = per_type.get(rtype, 0) + 1
        code = r.get("code") or {}
        code_text = str(code.get("text") or "").strip()
        if not code_text and isinstance(code.get("coding"), list) and code["coding"]:
            code_text = str(code["coding"][0].get("display") or "")
        date = _resource_clinical_date(r)
        if code_text:
            key = (rtype, code_text.lower())
            entry = series.setdefault(
                key,
                {"resource_count": 0, "first": None, "last": None, "values": [], "units": set()},
            )
            entry["resource_count"] += 1
            if date:
                if entry["first"] is None or date < entry["first"]:
                    entry["first"] = date
                if entry["last"] is None or date > entry["last"]:
                    entry["last"] = date
            q = _quantity(r)
            if q is not None:
                entry["values"].append(q[0])
                entry["units"].add(q[1])
        if rtype == "MedicationRequest":
            display = _med_display(r, by_id).strip().lower()
            if display:
                med_displays[display] = med_displays.get(display, 0) + 1
    series_out = []
    for (rtype, code_text), e in sorted(series.items(), key=lambda kv: -kv[1]["resource_count"]):
        row: dict[str, Any] = {
            "type": rtype,
            "code": code_text,
            "resource_count": e["resource_count"],
            "first": e["first"],
            "last": e["last"],
        }
        if e["values"] and len(e["units"]) == 1:
            unit = next(iter(e["units"]))
            row["value_min"] = min(e["values"])
            row["value_max"] = max(e["values"])
            row["value_sum"] = round(sum(e["values"]), 6)
            row["value_unit"] = unit
        series_out.append(row)
    summary = {
        "kind": "deterministic_aggregate_summary",
        "semantics": "resource counts per (type, code text) over ALL fetched resources pre-bounding; medication_distinct = distinct MedicationRequest display strings; sums only under a single consistent unit",
        "computed_over_resources": len(resources),
        "per_type_counts": dict(sorted(per_type.items())),
        "medication_distinct_count": len(med_displays),
        "medication_displays": dict(sorted(med_displays.items())[:60]),
        "code_series": series_out,
        "truncated": False,
    }
    while len(_json(summary)) > max_chars and summary["code_series"]:
        summary["code_series"] = summary["code_series"][: max(1, len(summary["code_series"]) // 2)]
        summary["truncated"] = True
    return summary


def bound_resources(
    resources: list[dict[str, Any]],
    *,
    temporal_policy: str,
    max_total_resources: int,
    max_packet_chars: int,
    endpoint_reserve: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enforce hard resource-count and char ceilings (review finding 2).

    Patient resources always survive. Per-type selection honors the temporal
    policy: `first_last` interleaves earliest/latest; `recent` keeps newest.
    Types are drained round-robin so one noisy type cannot evict the others.
    """
    projected = [project_resource(r) for r in resources]
    patients = [r for r in projected if r.get("resourceType") == "Patient"]
    rest = [r for r in projected if r.get("resourceType") != "Patient"]

    by_type: dict[str, list[dict[str, Any]]] = {}
    for r in rest:
        by_type.setdefault(str(r.get("resourceType") or "Unknown"), []).append(r)

    ordered_by_type: dict[str, list[dict[str, Any]]] = {}
    for rtype, items in by_type.items():
        items = sorted(items, key=_resource_clinical_date)
        if temporal_policy == "first_last":
            head, tail = 0, len(items) - 1
            order = []
            while head <= tail:
                order.append(items[head])
                head += 1
                if head <= tail:
                    order.append(items[tail])
                    tail -= 1
        else:
            order = list(reversed(items))  # newest first
        ordered_by_type[rtype] = order

    patient_chars = sum(len(_json(resource)) for resource in patients)
    if len(patients) > max_total_resources or patient_chars > max_packet_chars:
        raise ValueError(
            "Patient resources exceed the frozen packet count/character bounds"
        )
    selected: list[dict[str, Any]] = list(patients)
    chars = sum(len(_json(r)) for r in selected)
    budget_hit = False
    cursors = {rtype: 0 for rtype in sorted(ordered_by_type)}
    if endpoint_reserve:
        # Phase 1 (feature: endpoint-reserve): both temporal extremes of EVERY
        # type are packed before general round-robin, so a char-budget hit on
        # one noisy type can no longer evict another type's endpoint.
        reserve_n = 2 if temporal_policy == "first_last" else 1
        for rtype in sorted(cursors):
            order = ordered_by_type[rtype]
            while cursors[rtype] < min(reserve_n, len(order)) and len(selected) < max_total_resources:
                candidate = order[cursors[rtype]]
                candidate_chars = len(_json(candidate))
                if chars + candidate_chars > max_packet_chars:
                    budget_hit = True
                    break
                selected.append(candidate)
                chars += candidate_chars
                cursors[rtype] += 1
    while len(selected) < max_total_resources and any(
        cursors[t] < len(ordered_by_type[t]) for t in cursors
    ):
        progressed = False
        for rtype in sorted(cursors):
            if len(selected) >= max_total_resources:
                break
            i = cursors[rtype]
            if i >= len(ordered_by_type[rtype]):
                continue
            candidate = ordered_by_type[rtype][i]
            candidate_chars = len(_json(candidate))
            if chars + candidate_chars > max_packet_chars:
                budget_hit = True
                cursors[rtype] = len(ordered_by_type[rtype])  # this type is done
                continue
            selected.append(candidate)
            chars += candidate_chars
            cursors[rtype] = i + 1
            progressed = True
        if not progressed:
            break

    stats = {
        "input_count": len(resources),
        "kept_count": len(selected),
        "dropped_count": len(resources) - len(selected),
        "char_count": chars,
        "char_budget_hit": budget_hit or len(selected) < len(projected) and chars >= max_packet_chars,
        "max_total_resources": max_total_resources,
        "max_packet_chars": max_packet_chars,
        "temporal_policy": temporal_policy,
    }
    return selected, stats


def relax_query(path: str) -> str | None:
    """One deterministic relaxation step for a zero-result query, or None.

    Order: drop `code:text` (the most speculative filter — a term extracted
    from question text may not match the store's display vocabulary), then
    drop date-range params. Bare patient+type queries are safe because
    `bound_resources` caps the packet regardless of fetch size.
    """
    if "?" not in path:
        return None
    base, query = path.split("?", 1)
    params = query.split("&")
    for prefix in ("code:text=",):
        kept = [p for p in params if not p.startswith(prefix)]
        if len(kept) != len(params):
            return f"{base}?" + "&".join(kept)
    date_prefixes = tuple(f"{p}=ge" for p in set(RESOURCE_DATE_PARAM.values())) + tuple(
        f"{p}=le" for p in set(RESOURCE_DATE_PARAM.values())
    )
    kept = [p for p in params if not p.startswith(date_prefixes)]
    if len(kept) != len(params):
        return f"{base}?" + "&".join(kept)
    return None


def _safe_fetch_error(exc: Exception) -> dict[str, Any]:
    """Extract stable failure metadata without exception text, URLs, or bodies."""
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        code = getattr(exc, "code", None)
        status = code if isinstance(code, int) else None
    error: dict[str, Any] = {"type": type(exc).__name__}
    if status is not None:
        error["http_status"] = status
    return error


def fetch_resources(
    plan: list[dict[str, Any]],
    *,
    per_query_cap: int | None = None,
    client: Any | None = None,
) -> dict[str, list[dict[str, Any]]]:
    if client is None:
        from fhir_client import get_fhir_client

        client = get_fhir_client()
    out = {}
    for item in plan:
        path = item["path"]
        relaxation_attempts: list[dict[str, Any]] = []
        initial_result_count: int | None = None
        resources: list[dict[str, Any]] = []
        current: str | None = path
        attempt_index = 0
        while current is not None:
            try:
                resources = client.search_with_pagination(
                    current, max_results=per_query_cap
                )
            except Exception as exc:
                error = _safe_fetch_error(exc)
                receipt: dict[str, Any] = {
                    "status": "http_error" if "http_status" in error else "error",
                    "initial_result_count": initial_result_count,
                    "relaxation_attempts": relaxation_attempts,
                    "pre_bound_count": 0,
                    "retained_count": 0,
                    "dropped_count": 0,
                    "error": error,
                }
                item["fetch_receipt"] = receipt
                status_suffix = (
                    f" HTTP {error['http_status']}" if "http_status" in error else ""
                )
                raise PacketFetchError(
                    f"FHIR packet fetch failed: {error['type']}{status_suffix}"
                ) from None
            result_count = len(resources)
            if attempt_index == 0:
                initial_result_count = result_count
            else:
                relaxation_attempts.append(
                    {"path": current, "result_count": result_count}
                )
            if resources:
                break
            current = (
                None
                if item.get("relaxation_policy") == "none"
                else relax_query(current)
            )
            attempt_index += 1

        pre_bound_count = len(resources)
        if per_query_cap is not None and len(resources) > per_query_cap:
            resources = resources[:per_query_cap]
        retained_count = len(resources)
        out[path] = resources
        if relaxation_attempts:
            item["relaxation_attempts"] = [
                attempt["path"] for attempt in relaxation_attempts
            ]
        item["fetch_receipt"] = {
            "status": "ok",
            "initial_result_count": initial_result_count,
            "relaxation_attempts": relaxation_attempts,
            "pre_bound_count": pre_bound_count,
            "retained_count": retained_count,
            "dropped_count": pre_bound_count - retained_count,
        }
    return out


def build_packet_record(
    row: dict[str, Any],
    *,
    plan_only: bool,
    resources_by_query: dict[str, list[dict[str, Any]]] | None,
    count: int = 100,
    planner: str = "metadata-oracle",
    max_total_resources: int | None = None,
    max_packet_chars: int | None = None,
    plan: list[dict[str, Any]] | None = None,
    features: set[str] | frozenset[str] = frozenset(),
    reference_fetcher: Callable[[str, list[str]], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    features = validate_qt_features(features, planner=planner)
    max_total_resources, max_packet_chars = resolve_a6a_root_bounds(
        planner=planner,
        max_total_resources=max_total_resources,
        max_packet_chars=max_packet_chars,
    )
    if planner == "question-only":
        safe = {k: row.get(k) for k in QUESTION_ONLY_FIELDS}
        intent = qo_infer_intent(safe)
        kind = "a6a_question_only_packet"
    elif planner == "blunt-projection":
        safe = {k: row.get(k) for k in QUESTION_ONLY_FIELDS}
        intent = blunt_infer_intent(safe)
        kind = "a0prime_blunt_packet"
    else:
        safe = {k: v for k, v in row.items() if k not in GOLD_FIELDS}
        intent = infer_intent(safe)
        kind = "a6_metadata_oracle_packet"
    # Accept the caller's (possibly relaxation-annotated) plan so fetch-time
    # metadata survives into the packet; rebuild only if none was given.
    plan = plan if plan is not None else build_search_plan(safe, intent, count=count, features=features)
    resources_by_query = resources_by_query or {}
    resources = []
    for item in plan:
        resources.extend(resources_by_query.get(item["path"], []))
    resources = _dedupe_resources(resources)
    universe = list(resources)  # full fetched set, pre-bounding
    bounds_stats: dict[str, Any] | None = None
    if not plan_only and planner == "blunt-projection":
        resources, bounds_stats = blunt_bound(resources)
    elif not plan_only and max_total_resources is not None and max_packet_chars is not None:
        resources, bounds_stats = bound_resources(
            resources,
            temporal_policy=intent["temporal_policy"],
            max_total_resources=max_total_resources,
            max_packet_chars=max_packet_chars,
            endpoint_reserve="endpoint-reserve" in features,
        )
    root_fetch_receipt = None
    if not plan_only:
        root_fetch_receipt = {
            "pre_bound_count": len(universe),
            "retained_count": len(resources),
            "dropped_count": len(universe) - len(resources),
        }
    pinned_count = 0
    if not plan_only and "include-pinning" in features:
        resources, pinned_count = pin_reference_targets(resources, universe)
    summary_block: dict[str, Any] | None = None
    if not plan_only and "agg-summary" in features:
        summary_block = aggregate_summary([project_resource(r) for r in universe])
    reference_traversal: dict[str, Any] | None = None
    if (
        not plan_only
        and "micro-traversal" in features
        and is_microbiology_question(safe.get("question"))
    ):
        if reference_fetcher is None:
            raise ValueError("micro-traversal requires a reference_fetcher")
        traversal = traverse_exact_references(resources, fetch_by_ids=reference_fetcher)
        resources = _dedupe_resources(resources + traversal["resources"])
        # Keep a complete audit receipt even when no eligible edge exists. The
        # zero-edge outcome is part of the frozen traversal contract and lets
        # the preflight gate distinguish "ran and found nothing" from "did not
        # run" without exposing bookkeeping to the answering model.
        reference_traversal = {
            key: value for key, value in traversal.items() if key != "resources"
        }
    resource_ids = [rid for rid in (_resource_id(r) for r in resources) if rid]
    applied_features = features
    if features and features.issubset({"micro-vocab", "micro-traversal"}) and not is_microbiology_question(
        safe.get("question")
    ):
        # Arm identity remains in the manifest. A question where QT-4 does not
        # dispatch stores the literal A6a packet, making both packet SHA and
        # model prompt byte-identical without synthetic hash normalization.
        applied_features = frozenset()
    packet = {
        "kind": kind,
        "planner": intent.get("planner"),
        "features": sorted(applied_features),
        "pinned_reference_targets": pinned_count,
        "aggregate_summary": summary_block,
        "plan_only": plan_only,
        "resources": [] if plan_only else resources,
        "resource_count": 0 if plan_only else len(resources),
        "source_resource_ids": [] if plan_only else sorted(resource_ids),
        "source_queries": plan,
        "bounds": bounds_stats,
        "root_fetch_receipt": root_fetch_receipt,
    }
    if reference_traversal is not None:
        packet["reference_traversal"] = reference_traversal
    packet["sha256"] = sha256_text(_json(packet))
    return {
        "question_id": safe.get("question_id"),
        "question": safe.get("question"),
        "patient_fhir_id": safe.get("patient_fhir_id"),
        "assumption": safe.get("assumption"),
        "intent": intent,
        "packet": packet,
    }


def load_question_ids(path: Path) -> list[str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("question_ids"), list):
        raise ValueError("question spec must contain question_ids")
    question_ids = [str(item) for item in value["question_ids"]]
    if not question_ids or len(question_ids) != len(set(question_ids)):
        raise ValueError("question spec IDs must be non-empty and unique")
    return question_ids


def load_rows(
    input_path: Path,
    *,
    limit: int | None = None,
    split: str | None = "test",
    question_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    with input_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if split:
        rows = [r for r in rows if r.get("split") == split]
    if question_ids is not None:
        by_id = {str(row.get("question_id")): row for row in rows}
        missing = [question_id for question_id in question_ids if question_id not in by_id]
        if missing:
            raise ValueError(
                f"question spec contains {len(missing)} IDs missing from input"
            )
        rows = [by_id[question_id] for question_id in question_ids]
    if limit is not None:
        rows = rows[:limit]
    return rows


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_manifest(path: Path, *, input_path: Path, output_path: Path, args: argparse.Namespace, records: list[dict[str, Any]]) -> None:
    features = resolve_evidence_recipe(
        getattr(args, "evidence_recipe", None),
        explicit_features={
            f.strip()
            for f in getattr(args, "features", "").split(",")
            if f.strip()
        },
        planner=getattr(args, "planner", "metadata-oracle"),
    )
    max_total_resources, max_packet_chars = resolve_a6a_root_bounds(
        planner=getattr(args, "planner", "metadata-oracle"),
        max_total_resources=getattr(args, "max_total_resources", None),
        max_packet_chars=getattr(args, "max_packet_chars", None),
    )
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
            "question_spec": (
                {
                    "path": str(args.question_spec),
                    "sha256": sha256_file(args.question_spec),
                }
                if getattr(args, "question_spec", None)
                else None
            ),
            "planner": getattr(args, "planner", "metadata-oracle"),
            "features": sorted(features),
            "evidence_recipe": (
                {
                    "id": args.evidence_recipe,
                    "status": "promoted_on_qt4_valid374",
                    "features": sorted(features),
                    "promotion_result": "docs/results/QT4_VALID374_RESULT.md",
                }
                if getattr(args, "evidence_recipe", None)
                else None
            ),
            "planner_version": QO_PLANNER_VERSION if getattr(args, "planner", "") == "question-only" else "metadata-v1",
            "max_total_resources": max_total_resources,
            "max_packet_chars": max_packet_chars,
            "micro_vocabulary": (
                {"version": MICRO_VOCABULARY_VERSION, "code_text_terms": list(MICRO_CODE_TEXT_TERMS)}
                if "micro-vocab" in features
                else None
            ),
            "micro_dispatcher": (
                {
                    "version": MICRO_DISPATCHER_VERSION,
                    "question_terms": list(MICRO_QUESTION_TERMS),
                }
                if "micro-vocab" in features
                else None
            ),
            "reference_traversal": (
                {
                    "version": MICRO_TRAVERSAL_VERSION,
                    "paths": {
                        source: [f"{field} -> {target}" for field, target in paths]
                        for source, paths in MICRO_REFERENCE_PATHS.items()
                    },
                    "max_depth": MICRO_TRAVERSAL_MAX_DEPTH,
                    "max_resources": MICRO_TRAVERSAL_MAX_RESOURCES,
                    "max_serialized_bytes": MICRO_TRAVERSAL_MAX_SERIALIZED_BYTES,
                    "max_path_receipts": MICRO_TRAVERSAL_MAX_PATH_RECEIPTS,
                    "max_path_receipt_bytes": MICRO_TRAVERSAL_MAX_PATH_RECEIPT_BYTES,
                }
                if "micro-traversal" in features
                else None
            ),
        },
        "questions": len(records),
        "packet_hashes": {str(r["question_id"]): r["packet"]["sha256"] for r in records},
    }
    if not getattr(args, "evidence_recipe", None):
        # Preserve the historical manifest schema byte-for-field: old A6/QT
        # entrypoints did not carry a recipe key at all.
        manifest["config"].pop("evidence_recipe")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main(*, default_evidence_recipe: str | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build A6 query-aware frozen packets.")
    parser.add_argument("--input", type=Path, default=Path("final_dataset/full_test409.csv"))
    parser.add_argument("--output", type=Path, default=Path("runs/a6_query_aware_packets.jsonl"))
    parser.add_argument("--manifest", type=Path, default=Path("runs/a6_query_aware_manifest.json"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--split", default="test")
    parser.add_argument(
        "--question-spec",
        type=Path,
        default=None,
        help="optional frozen JSON question_ids schedule to build in exact order",
    )
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument(
        "--planner",
        choices=["question-only", "metadata-oracle", "blunt-projection"],
        default="question-only",
        help="question-only = A6a primary arm (whitelist: question/patient/assumption); metadata-oracle = ceiling arm using benchmark-construction metadata; blunt-projection = A0' control (query-blind, per-type recency cap)",
    )
    parser.add_argument("--max-total-resources", type=int, default=A6A_MAX_TOTAL_RESOURCES)
    parser.add_argument("--max-packet-chars", type=int, default=A6A_MAX_PACKET_CHARS)
    parser.add_argument(
        "--features",
        default="",
        help=f"comma-separated single-treatment toggles on the frozen base: {','.join(QT_FEATURES)}",
    )
    parser.add_argument(
        "--evidence-recipe",
        choices=EVIDENCE_RECIPES,
        default=default_evidence_recipe,
        help=(
            "versioned promoted product recipe; mutually exclusive with "
            "--features. Historical a6_packet_builder.py calls default to no recipe"
        ),
    )
    args = parser.parse_args()

    try:
        features = resolve_evidence_recipe(
            args.evidence_recipe,
            explicit_features={
                f.strip() for f in args.features.split(",") if f.strip()
            },
            planner=args.planner,
        )
    except ValueError as exc:
        parser.error(str(exc))
    try:
        args.max_total_resources, args.max_packet_chars = resolve_a6a_root_bounds(
            planner=args.planner,
            max_total_resources=args.max_total_resources,
            max_packet_chars=args.max_packet_chars,
        )
    except ValueError as exc:
        parser.error(str(exc))
    try:
        question_ids = (
            load_question_ids(args.question_spec) if args.question_spec else None
        )
        rows = load_rows(
            args.input,
            limit=args.limit,
            split=args.split,
            question_ids=question_ids,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    records = []
    client = None
    if not args.plan_only:
        from fhir_client import get_fhir_client

        client = get_fhir_client()
    reference_fetcher = None
    if client is not None and "micro-traversal" in features:
        def fetch_references(resource_type: str, ids: list[str]) -> list[dict[str, Any]]:
            return client.get_resources_by_resource_ids(resource_type, ids, max_size=len(ids))

        reference_fetcher = fetch_references
    for row in rows:
        if args.planner == "question-only":
            qrow = {k: row.get(k) for k in QUESTION_ONLY_FIELDS}
            plan = build_search_plan(qrow, qo_infer_intent(qrow), count=args.count, features=features)
        elif args.planner == "blunt-projection":
            qrow = {k: row.get(k) for k in QUESTION_ONLY_FIELDS}
            plan = build_search_plan(qrow, blunt_infer_intent(qrow), count=args.count, features=features)
        else:
            plan = build_search_plan(row, count=args.count, features=features)
        per_query_cap = 4 * BLUNT_PER_TYPE_CAP if args.planner == "blunt-projection" else 4 * args.max_total_resources
        resources = {} if args.plan_only else fetch_resources(plan, per_query_cap=per_query_cap, client=client)
        records.append(
            build_packet_record(
                row,
                plan_only=args.plan_only,
                resources_by_query=resources,
                count=args.count,
                planner=args.planner,
                max_total_resources=args.max_total_resources,
                max_packet_chars=args.max_packet_chars,
                plan=plan,
                features=features,
                reference_fetcher=reference_fetcher,
            )
        )
    write_jsonl(args.output, records)
    write_manifest(args.manifest, input_path=args.input, output_path=args.output, args=args, records=records)
    print(json.dumps({"output": str(args.output), "manifest": str(args.manifest), "records": len(records), "plan_only": args.plan_only}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
