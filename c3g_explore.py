#!/usr/bin/env python3
"""THROWAWAY PROTOTYPE: flat selected roots versus bounded graph closure.

This is development tooling for burned FHIR-AgentBench data.  It intentionally
does not implement the confirmatory C3G controller, policy service, persistent
graph index, or product API.  Its one question is:

    Does adding deterministic outbound reference closure help when the exact
    same selected root resources are already visible to the answering model?

The build command emits paired packet JSONL files.  The flat arm preserves the
input packet object unchanged.  The graph arm only adds resolved target resources
and replayable path citations.  An audit-only census records fetches, limits,
and a mechanism-rich sample; it is never included in the model packet.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Iterable

import a6_packet_builder as a6
from fhir_client import get_fhir_client


PROTOTYPE_VERSION = "c3g-explore-v0"
DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_TARGETS = 24
DEFAULT_MAX_EDGES = 96
DEFAULT_MAX_CITATIONS = 48
DEFAULT_MAX_ADDED_BYTES = 24_000

RELATIVE_REFERENCE = re.compile(
    r"^(?P<type>[A-Za-z][A-Za-z0-9]*)/(?P<id>[A-Za-z0-9\-.]{1,64})"
    r"(?:/_history/(?P<version>[A-Za-z0-9\-.]{1,64}))?$"
)

# Mirrored from Bonfire's experimental clinical-reference-v1 semantic catalog.
# Keeping it here makes this prototype file-backed and disposable.
CLINICAL_REFERENCE_RULES: tuple[tuple[str, re.Pattern[str], frozenset[str]], ...] = (
    ("Condition", re.compile(r"^/encounter/reference$"), frozenset({"Encounter"})),
    ("Condition", re.compile(r"^/subject/reference$"), frozenset({"Patient"})),
    ("DiagnosticReport", re.compile(r"^/encounter/reference$"), frozenset({"Encounter"})),
    ("DiagnosticReport", re.compile(r"^/result/\d+/reference$"), frozenset({"Observation"})),
    ("DiagnosticReport", re.compile(r"^/specimen/\d+/reference$"), frozenset({"Specimen"})),
    ("DiagnosticReport", re.compile(r"^/subject/reference$"), frozenset({"Patient"})),
    ("Encounter", re.compile(r"^/diagnosis/\d+/condition/reference$"), frozenset({"Condition"})),
    ("Encounter", re.compile(r"^/partOf/reference$"), frozenset({"Encounter"})),
    ("Encounter", re.compile(r"^/reasonReference/\d+/reference$"), frozenset({"Condition"})),
    ("Encounter", re.compile(r"^/subject/reference$"), frozenset({"Patient"})),
    ("MedicationRequest", re.compile(r"^/encounter/reference$"), frozenset({"Encounter"})),
    ("MedicationRequest", re.compile(r"^/medicationReference/reference$"), frozenset({"Medication"})),
    ("MedicationRequest", re.compile(r"^/reasonReference/\d+/reference$"), frozenset({"Condition"})),
    ("MedicationRequest", re.compile(r"^/subject/reference$"), frozenset({"Patient"})),
    ("Observation", re.compile(r"^/encounter/reference$"), frozenset({"Encounter"})),
    ("Observation", re.compile(r"^/hasMember/\d+/reference$"), frozenset({"Observation"})),
    ("Observation", re.compile(r"^/specimen/reference$"), frozenset({"Specimen"})),
    ("Observation", re.compile(r"^/subject/reference$"), frozenset({"Patient"})),
    ("Procedure", re.compile(r"^/encounter/reference$"), frozenset({"Encounter"})),
    ("Procedure", re.compile(r"^/report/\d+/reference$"), frozenset({"DiagnosticReport", "DocumentReference"})),
    ("Procedure", re.compile(r"^/subject/reference$"), frozenset({"Patient"})),
    ("ServiceRequest", re.compile(r"^/encounter/reference$"), frozenset({"Encounter"})),
    ("ServiceRequest", re.compile(r"^/specimen/\d+/reference$"), frozenset({"Specimen"})),
    ("ServiceRequest", re.compile(r"^/subject/reference$"), frozenset({"Patient"})),
    ("Specimen", re.compile(r"^/parent/\d+/reference$"), frozenset({"Specimen"})),
    ("Specimen", re.compile(r"^/request/\d+/reference$"), frozenset({"ServiceRequest"})),
    ("Specimen", re.compile(r"^/subject/reference$"), frozenset({"Patient"})),
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def resource_ref(resource: dict[str, Any]) -> str | None:
    resource_type = resource.get("resourceType")
    resource_id = resource.get("id")
    if not isinstance(resource_type, str) or not isinstance(resource_id, str):
        return None
    return f"{resource_type}/{resource_id}"


def pointer_segment(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def explicit_references(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[str, str]]:
    """Yield pointers to the reference string itself, matching Bonfire."""
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from explicit_references(child, (*path, str(index)))
        return
    if not isinstance(value, dict):
        return
    for key in sorted(value):
        child = value[key]
        next_path = (*path, key)
        if key == "reference" and isinstance(child, str) and RELATIVE_REFERENCE.fullmatch(child):
            yield "/" + "/".join(pointer_segment(part) for part in next_path), child
        yield from explicit_references(child, next_path)


def allowed_edge(source_type: str, pointer: str, target_type: str) -> bool:
    return any(
        source_type == candidate_source
        and target_type in target_types
        and pointer_pattern.fullmatch(pointer)
        for candidate_source, pointer_pattern, target_types in CLINICAL_REFERENCE_RULES
    )


def patient_refs(resource: dict[str, Any]) -> set[str]:
    refs = set()
    for field in ("subject", "patient", "beneficiary"):
        value = resource.get(field)
        if isinstance(value, dict):
            reference = value.get("reference")
            if isinstance(reference, str) and reference.startswith("Patient/"):
                refs.add(reference)
    return refs


def patient_consistent(resource: dict[str, Any], patient_ref: str) -> bool:
    refs = patient_refs(resource)
    return not refs or refs == {patient_ref}


def edges_for(resource: dict[str, Any]) -> list[dict[str, str]]:
    source = resource_ref(resource)
    if source is None:
        return []
    source_type = source.split("/", 1)[0]
    edges = []
    for pointer, requested in explicit_references(resource):
        match = RELATIVE_REFERENCE.fullmatch(requested)
        if match is None:
            continue
        target_type = match.group("type")
        if not allowed_edge(source_type, pointer, target_type):
            continue
        target = f"{target_type}/{match.group('id')}"
        edges.append(
            {
                "from": source,
                "path": pointer,
                "to": target,
                "requested": requested,
            }
        )
    return sorted(edges, key=canonical_bytes)


def edge_priority(question: str, edge: dict[str, str]) -> tuple[int, str, str]:
    """Question-only scheduling; it changes traversal order, never the roots."""
    text = question.casefold()
    path = edge["path"]
    if any(term in text for term in ("microbiolog", "culture", "specimen", "organism", "smear", "gram stain")):
        families = ("/result/", "/hasMember/", "/specimen", "/request/", "/encounter", "/subject")
    elif any(term in text for term in ("medication", "drug", "prescri", "dose", "route", "tablet", "infusion")):
        families = ("/medicationReference", "/reasonReference/", "/encounter", "/subject")
    elif any(term in text for term in ("procedure", "surgery", "operation", "undergo", "intubat", "dialysis")):
        families = ("/report/", "/reasonReference/", "/encounter", "/subject")
    elif any(term in text for term in ("visit", "admission", "admit", "discharge", "encounter", "hospital", "icu", "stay")):
        families = ("/encounter", "/partOf", "/diagnosis/", "/reasonReference/", "/subject")
    elif any(term in text for term in ("gender", "sex", "age", "birth", "race", "ethnic", "marital", "language")):
        families = ("/subject", "/encounter", "/partOf")
    else:
        families = (
            "/result/",
            "/hasMember/",
            "/specimen",
            "/medicationReference",
            "/report/",
            "/reasonReference/",
            "/encounter",
            "/partOf",
            "/diagnosis/",
            "/subject",
        )
    rank = next((index for index, family in enumerate(families) if family in path), len(families))
    return rank, path, edge["to"]


def round_robin_edges(resources: list[dict[str, Any]], *, question: str) -> list[dict[str, str]]:
    queues = deque(
        deque(sorted(edges_for(resource), key=lambda edge: edge_priority(question, edge)))
        for resource in sorted(resources, key=lambda item: resource_ref(item) or "")
    )
    ordered: list[dict[str, str]] = []
    while queues:
        current = queues.popleft()
        if not current:
            continue
        ordered.append(current.popleft())
        if current:
            queues.append(current)
    return ordered


def fetch_by_refs(client: Any, refs: list[str]) -> dict[str, dict[str, Any]]:
    by_type: dict[str, list[str]] = defaultdict(list)
    for reference in refs:
        resource_type, resource_id = reference.split("/", 1)
        by_type[resource_type].append(resource_id)
    fetched: dict[str, dict[str, Any]] = {}
    for resource_type in sorted(by_type):
        resources = client.get_resources_by_resource_ids(resource_type, sorted(set(by_type[resource_type])))
        for resource in resources:
            reference = resource_ref(resource)
            if reference is not None:
                fetched[reference] = a6.project_resource(resource)
    return fetched


def compile_closure(
    roots: list[dict[str, Any]],
    *,
    patient_ref: str,
    fetcher: Callable[[list[str]], dict[str, dict[str, Any]]],
    max_depth: int,
    max_targets: int,
    max_edges: int,
    max_citations: int,
    max_added_bytes: int,
    question: str,
) -> dict[str, Any]:
    roots_by_ref = {
        reference: resource
        for resource in roots
        if (reference := resource_ref(resource)) is not None
    }
    included = dict(roots_by_ref)
    added: dict[str, dict[str, Any]] = {}
    inspected: set[str] = set()
    audit_edges: list[dict[str, Any]] = []
    outcomes: set[str] = set()
    added_bytes = 0
    frontier = [roots_by_ref[reference] for reference in sorted(roots_by_ref)]

    for depth in range(1, max_depth + 1):
        candidates = round_robin_edges(frontier, question=question)
        if not candidates:
            break
        if len(audit_edges) + len(candidates) > max_edges:
            candidates = candidates[: max(0, max_edges - len(audit_edges))]
            outcomes.add("edge_limit")

        wanted: list[str] = []
        for edge in candidates:
            target = edge["to"]
            if target in included or target in inspected or target in wanted:
                continue
            if len(inspected) + len(wanted) >= max_targets:
                outcomes.add("target_limit")
                continue
            wanted.append(target)
        fetched = fetcher(wanted) if wanted else {}
        inspected.update(wanted)
        next_frontier: dict[str, dict[str, Any]] = {}

        for edge in candidates:
            target = edge["to"]
            if target in included:
                status = "already_present"
            elif target not in inspected:
                status = "max_targets"
            elif target not in fetched:
                status = "missing"
            elif not patient_consistent(fetched[target], patient_ref):
                status = "patient_mismatch"
                outcomes.add("patient_mismatch")
            else:
                candidate = fetched[target]
                candidate_size = len(canonical_bytes(candidate))
                if added_bytes + candidate_size > max_added_bytes:
                    status = "max_added_bytes"
                    outcomes.add("byte_limit")
                else:
                    status = "fetched"
                    included[target] = candidate
                    added[target] = candidate
                    next_frontier[target] = candidate
                    added_bytes += candidate_size
            receipt = {
                "depth": depth,
                "from": edge["from"],
                "path": edge["path"],
                "to": target,
                "status": status,
            }
            audit_edges.append(receipt)
        frontier = [next_frontier[reference] for reference in sorted(next_frontier)]
        if not frontier or len(audit_edges) >= max_edges:
            break

    successful = [edge for edge in audit_edges if edge["status"] in {"fetched", "already_present"}]
    successful.sort(
        key=lambda edge: (
            0 if edge["status"] == "fetched" else 1,
            edge["depth"],
            edge["from"],
            edge["path"],
            edge["to"],
        )
    )
    citations = successful[:max_citations]
    if len(successful) > len(citations):
        outcomes.add("citation_limit")
    return {
        "added_resources": [added[reference] for reference in sorted(added)],
        "path_receipts": citations,
        "audit_edges": audit_edges,
        "receipt": {
            "version": PROTOTYPE_VERSION,
            "root_count": len(roots_by_ref),
            "root_sha256": digest(sorted(roots_by_ref)),
            "added_resource_count": len(added),
            "added_resource_refs": sorted(added),
            "added_serialized_bytes": added_bytes,
            "inspected_target_count": len(inspected),
            "edge_count": len(audit_edges),
            "citation_count": len(citations),
            "outcomes": sorted(outcomes),
            "limits": {
                "max_depth": max_depth,
                "max_targets": max_targets,
                "max_edges": max_edges,
                "max_citations": max_citations,
                "max_added_bytes": max_added_bytes,
            },
        },
    }


def load_jsonl(
    path: Path,
    *,
    limit: int | None,
    question_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    records = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                record = json.loads(line)
                if question_ids and str(record.get("question_id")) not in question_ids:
                    continue
                records.append(record)
                if limit is not None and len(records) >= limit:
                    break
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def graph_packet(flat_record: dict[str, Any], closure: dict[str, Any]) -> dict[str, Any]:
    record = copy.deepcopy(flat_record)
    packet = record["packet"]
    roots = packet.get("resources")
    if not isinstance(roots, list):
        raise ValueError("input packet has no resource list")
    packet["resources"] = a6._dedupe_resources([*roots, *closure["added_resources"]])
    packet["resource_count"] = len(packet["resources"])
    packet["source_resource_ids"] = sorted(
        reference
        for resource in packet["resources"]
        if (reference := resource_ref(resource)) is not None
    )
    packet["reference_traversal"] = {
        "kind": "bounded_clinical_reference_closure",
        "version": PROTOTYPE_VERSION,
        "path_receipts": closure["path_receipts"],
    }
    packet.pop("sha256", None)
    packet["sha256"] = digest(packet)
    return record


def choose_sample(rows: list[dict[str, Any]], sample_size: int, controls: int) -> list[str]:
    positive = [row for row in rows if row["receipt"]["added_resource_count"] > 0]
    zero = [row for row in rows if row["receipt"]["added_resource_count"] == 0]
    positive.sort(
        key=lambda row: (
            -row["receipt"]["added_resource_count"],
            -row["receipt"]["citation_count"],
            row["question_id"],
        )
    )
    zero.sort(key=lambda row: row["question_id"])
    control_count = min(controls, sample_size, len(zero))
    selected = positive[: max(0, sample_size - control_count)] + zero[:control_count]
    return [str(row["question_id"]) for row in selected]


def build(args: argparse.Namespace) -> int:
    requested = list(args.question_id)
    if args.question_id_file:
        requested.extend(
            line.strip()
            for line in args.question_id_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    requested_ids = set(requested) if requested else None
    records = load_jsonl(args.input, limit=args.limit, question_ids=requested_ids)
    if requested_ids:
        found = {str(record.get("question_id")) for record in records}
        missing = sorted(requested_ids - found)
        if missing:
            raise ValueError(f"input packet file is missing question IDs: {missing}")
    client = get_fhir_client()
    flat_records: list[dict[str, Any]] = []
    graph_records: list[dict[str, Any]] = []
    census_rows: list[dict[str, Any]] = []

    for index, record in enumerate(records, 1):
        roots = record.get("packet", {}).get("resources")
        patient_id = str(record.get("patient_fhir_id") or "")
        if not isinstance(roots, list) or not patient_id:
            raise ValueError(f"invalid packet record: {record.get('question_id')}")
        closure = compile_closure(
            roots,
            patient_ref=f"Patient/{patient_id}",
            fetcher=lambda refs: fetch_by_refs(client, refs),
            max_depth=args.max_depth,
            max_targets=args.max_targets,
            max_edges=args.max_edges,
            max_citations=args.max_citations,
            max_added_bytes=args.max_added_bytes,
            question=str(record.get("question") or ""),
        )
        flat_records.append(record)
        graph_records.append(graph_packet(record, closure))
        census_rows.append(
            {
                "question_id": record.get("question_id"),
                "question": record.get("question"),
                "receipt": closure["receipt"],
                "audit_edges": closure["audit_edges"],
            }
        )
        print(
            f"[{index}/{len(records)}] {record.get('question_id')} "
            f"roots={len(roots)} added={closure['receipt']['added_resource_count']} "
            f"edges={closure['receipt']['edge_count']}",
            flush=True,
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    flat_path = args.output_dir / "flat_packets.jsonl"
    graph_path = args.output_dir / "graph_packets.jsonl"
    census_path = args.output_dir / "census.json"
    write_jsonl(flat_path, flat_records)
    write_jsonl(graph_path, graph_records)
    sample = choose_sample(census_rows, args.sample_size, args.control_count)
    census = {
        "kind": "throwaway_c3g_exploration_census",
        "version": PROTOTYPE_VERSION,
        "interpretation": "development-only; burned data; not a confirmatory result",
        "input": {"path": str(args.input), "sha256": a6.sha256_file(args.input)},
        "outputs": {
            "flat": {"path": str(flat_path), "sha256": a6.sha256_file(flat_path)},
            "graph": {"path": str(graph_path), "sha256": a6.sha256_file(graph_path)},
        },
        "question_count": len(census_rows),
        "questions_with_added_targets": sum(row["receipt"]["added_resource_count"] > 0 for row in census_rows),
        "total_added_targets": sum(row["receipt"]["added_resource_count"] for row in census_rows),
        "sample_question_ids": sample,
        "rows": census_rows,
    }
    census_path.write_text(json.dumps(census, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"census": str(census_path), "sample_question_ids": sample}, indent=2))
    return 0


def inspect(args: argparse.Namespace) -> int:
    census = json.loads(args.census.read_text(encoding="utf-8"))
    rows = census["rows"]
    by_id = {str(row["question_id"]): row for row in rows}
    print("C3G throwaway exploration")
    print(f"questions: {census['question_count']}")
    print(f"with graph additions: {census['questions_with_added_targets']}")
    print(f"added targets: {census['total_added_targets']}")
    print("sample:")
    for question_id in census["sample_question_ids"]:
        row = by_id[question_id]
        receipt = row["receipt"]
        print(
            f"  {question_id}  +{receipt['added_resource_count']} targets  "
            f"{receipt['citation_count']} citations  {row['question']}"
        )
    if args.question_id:
        row = by_id[args.question_id]
        print(json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build", help="compile paired flat/graph packets")
    build_parser.add_argument("--input", type=Path, required=True)
    build_parser.add_argument("--output-dir", type=Path, required=True)
    build_parser.add_argument("--limit", type=int)
    build_parser.add_argument("--question-id", action="append", default=[])
    build_parser.add_argument("--question-id-file", type=Path)
    build_parser.add_argument("--sample-size", type=int, default=24)
    build_parser.add_argument("--control-count", type=int, default=6)
    build_parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    build_parser.add_argument("--max-targets", type=int, default=DEFAULT_MAX_TARGETS)
    build_parser.add_argument("--max-edges", type=int, default=DEFAULT_MAX_EDGES)
    build_parser.add_argument("--max-citations", type=int, default=DEFAULT_MAX_CITATIONS)
    build_parser.add_argument("--max-added-bytes", type=int, default=DEFAULT_MAX_ADDED_BYTES)
    build_parser.set_defaults(func=build)
    inspect_parser = commands.add_parser("inspect", help="terminal inspection UI")
    inspect_parser.add_argument("--census", type=Path, required=True)
    inspect_parser.add_argument("--question-id")
    inspect_parser.set_defaults(func=inspect)
    return root


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
