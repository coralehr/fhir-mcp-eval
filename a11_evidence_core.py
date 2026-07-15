#!/usr/bin/env python3
"""Hardened, deterministic evidence projection primitives for A11 v2.

This module is intentionally separate from ``a11_path_required_benchmark.py``.
That predecessor is a published mechanism artifact; A11 v2 must not silently
reinterpret its historical packets while adding stronger patient, version,
privacy, and byte-bound guarantees.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict, deque
from typing import Any, Iterable


CORE_VERSION = "a11-evidence-core-v1"
MAX_DISCOVERED_EDGES = 128
MAX_PATH_CITATIONS = 256
REGISTERED_REFERENCE_PATHS = frozenset(
    {
        ("Observation", "hasMember", "Observation", "repeating"),
        ("Observation", "specimen", "Specimen", "singular"),
        ("DiagnosticReport", "result", "Observation", "repeating"),
        ("DiagnosticReport", "specimen", "Specimen", "singular"),
    }
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_line(value: Any) -> bytes:
    return canonical_bytes(value) + b"\n"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resource_ref(resource: dict[str, Any]) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


def parse_relative_reference(reference: str) -> tuple[str, str, str | None] | None:
    if not reference or reference.startswith(("#", "urn:", "http://", "https://")):
        return None
    parts = reference.split("/")
    if len(parts) == 2 and all(parts):
        return parts[0], parts[1], None
    if len(parts) == 4 and parts[2] == "_history" and all(parts):
        return parts[0], parts[1], parts[3]
    return None


def escape_json_pointer(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def iter_explicit_references(
    value: Any, pointer: str = ""
) -> Iterable[tuple[str, str]]:
    """Yield JSON pointers to FHIR Reference objects and their exact values."""

    if isinstance(value, dict):
        reference = value.get("reference")
        if isinstance(reference, str) and parse_relative_reference(reference):
            yield pointer or "/", reference
        for key in sorted(value):
            yield from iter_explicit_references(
                value[key], f"{pointer}/{escape_json_pointer(key)}"
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_explicit_references(item, f"{pointer}/{index}")


def _unescape_json_pointer(segment: str) -> str:
    return segment.replace("~1", "/").replace("~0", "~")


def _reference_object_at_pointer(resource: dict[str, Any], pointer: str) -> dict[str, Any]:
    value: Any = resource
    for raw_segment in pointer.strip("/").split("/") if pointer != "/" else []:
        segment = _unescape_json_pointer(raw_segment)
        value = value[int(segment)] if isinstance(value, list) else value[segment]
    if not isinstance(value, dict):
        raise ValueError(f"reference pointer does not address an object: {pointer}")
    return value


def _resolve(
    index: dict[str, dict[str, Any]], reference: str
) -> tuple[str, dict[str, Any], str | None] | None:
    parsed = parse_relative_reference(reference)
    if not parsed:
        return None
    resource_type, resource_id, requested_version = parsed
    canonical_ref = f"{resource_type}/{resource_id}"
    resource = index.get(canonical_ref)
    if resource is None:
        return None
    resolved_version = str(resource.get("meta", {}).get("versionId", "")) or None
    if requested_version is not None and requested_version != resolved_version:
        return None
    return canonical_ref, resource, requested_version


def _patient_references(resource: dict[str, Any]) -> set[str]:
    references: set[str] = set()
    for field in ("subject", "patient", "beneficiary"):
        value = resource.get(field)
        if isinstance(value, dict) and isinstance(value.get("reference"), str):
            reference = value["reference"]
            if reference.startswith("Patient/"):
                references.add(reference)
    return references


def _target_is_patient_authorized(
    target_ref: str, target: dict[str, Any], patient_ref: str
) -> bool:
    if target.get("resourceType") == "Patient":
        return target_ref == patient_ref
    patient_refs = _patient_references(target)
    has_patient_bearing_field = any(
        isinstance(target.get(field), dict)
        for field in ("subject", "patient", "beneficiary")
    )
    return (
        patient_refs == {patient_ref}
        if has_patient_bearing_field
        else True
    )


def _registered_relation(
    source_type: str, pointer: str, target_type: str
) -> bool:
    segments = [
        _unescape_json_pointer(segment)
        for segment in pointer.split("/")
        if segment
    ]
    if len(segments) == 1:
        shape = "singular"
    elif len(segments) == 2 and segments[1].isdigit():
        shape = "repeating"
    else:
        return False
    return (source_type, segments[0], target_type, shape) in REGISTERED_REFERENCE_PATHS


def visible_index(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return practice-visible resources only after purpose authorization."""

    if case["principal"]["purpose"] not in case["allowed_purposes"]:
        return {}
    practice_id = case["principal"]["practice_id"]
    return {
        resource_ref(entry["resource"]): entry["resource"]
        for entry in case["resources"]
        if entry["practice_id"] == practice_id
    }


def authorized_seed_refs(
    case: dict[str, Any], index: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Require every seed root to explicitly belong to the requested patient."""

    patient_ref = case["patient_ref"]
    authorized: list[str] = []
    outcomes: set[str] = set()
    for seed_ref in sorted(case["seed_refs"]):
        resource = index.get(seed_ref)
        if resource is None:
            outcomes.add("seed_unavailable")
            continue
        patient_refs = _patient_references(resource)
        if patient_refs == {patient_ref}:
            authorized.append(seed_ref)
        elif patient_refs:
            outcomes.add("cross_patient_seed")
        else:
            outcomes.add("ambiguous_patient_seed")
    return authorized, sorted(outcomes)


def _redacted_resources(
    included: dict[str, dict[str, Any]],
    index: dict[str, dict[str, Any]],
    allowed_locations: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Remove identifiers for every reference that is not in the model packet."""

    projected: list[dict[str, Any]] = []
    for source_ref in sorted(included):
        resource = copy.deepcopy(included[source_ref])
        references = sorted(
            iter_explicit_references(resource),
            key=lambda item: item[0].count("/"),
            reverse=True,
        )
        for pointer, reference in references:
            resolved = _resolve(index, reference)
            if (
                (source_ref, pointer) in allowed_locations
                and resolved is not None
                and resolved[0] in included
            ):
                continue
            reference_object = _reference_object_at_pointer(resource, pointer)
            reference_object.clear()
            reference_object["display"] = "Reference withheld"
        projected.append(resource)
    return projected


def _model_step(edge: dict[str, Any]) -> dict[str, Any]:
    step = {
        "source": edge["source"],
        "json_pointer": edge["json_pointer"],
        "target": edge["resolved_reference"],
        "target_type": edge["target_type"],
    }
    if edge["state"] == "available" and (
        edge["requested_reference"] != edge["resolved_reference"]
    ):
        step["requested_reference"] = edge["requested_reference"]
    return step


def _audit_step(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": edge["source"],
        "json_pointer": edge["json_pointer"],
        "requested_reference": edge["requested_reference"],
        "resolved_reference": edge["resolved_reference"],
        "target_type": edge["target_type"],
        "state": edge["state"],
    }


def _path_citations(
    seed_refs: list[str],
    edges: list[dict[str, Any]],
    max_depth: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        adjacency[edge["source"]].append(edge)
    for source in adjacency:
        adjacency[source].sort(key=canonical_bytes)

    model_citations: dict[bytes, dict[str, Any]] = {}
    audit_citations: dict[bytes, dict[str, Any]] = {}
    path_limit_reached = False

    def walk(
        source_ref: str,
        model_path: list[dict[str, Any]],
        audit_path: list[dict[str, Any]],
        visited: frozenset[str],
    ) -> None:
        nonlocal path_limit_reached
        if path_limit_reached:
            return
        if len(model_path) >= max_depth:
            return
        for edge in adjacency.get(source_ref, []):
            next_model = [*model_path, _model_step(edge)]
            next_audit = [*audit_path, _audit_step(edge)]
            model_citation = {
                "state": edge["state"],
                "target": edge["resolved_reference"],
                "target_type": edge["target_type"],
                "steps": next_model,
            }
            audit_citation = {
                "state": edge["state"],
                "requested_target": edge["requested_reference"],
                "resolved_target": edge["resolved_reference"],
                "steps": next_audit,
            }
            model_key = canonical_bytes(model_citation)
            audit_key = canonical_bytes(audit_citation)
            if (
                model_key not in model_citations
                and len(model_citations) >= MAX_PATH_CITATIONS
            ):
                path_limit_reached = True
                return
            model_citations[model_key] = model_citation
            audit_citations[audit_key] = audit_citation
            target = edge["resolved_reference"]
            if (
                edge["state"] == "available"
                and target is not None
                and target not in visited
                and len(next_model) < max_depth
            ):
                walk(target, next_model, next_audit, visited | {target})

    for seed_ref in seed_refs:
        if path_limit_reached:
            break
        walk(seed_ref, [], [], frozenset({seed_ref}))
    return (
        [model_citations[key] for key in sorted(model_citations)],
        [audit_citations[key] for key in sorted(audit_citations)],
        path_limit_reached,
    )


def project_star(case: dict[str, Any]) -> dict[str, Any]:
    index = visible_index(case)
    seeds, seed_outcomes = authorized_seed_refs(case, index)
    included: dict[str, dict[str, Any]] = {}
    patient = index.get(case["patient_ref"])
    if patient is not None:
        included[case["patient_ref"]] = patient
    for seed_ref in seeds:
        included[seed_ref] = index[seed_ref]
    outcomes = set(seed_outcomes)
    if case["principal"]["purpose"] not in case["allowed_purposes"]:
        outcomes.add("purpose_denial")
    allowed_locations: set[tuple[str, str]] = set()
    for source_ref, resource in included.items():
        for pointer, reference in iter_explicit_references(resource):
            resolved = _resolve(index, reference)
            if resolved is not None and resolved[0] == case["patient_ref"]:
                allowed_locations.add((source_ref, pointer))
    return {
        "resources": _redacted_resources(included, index, allowed_locations),
        "path_citations": [],
        "audit_path_citations": [],
        "root_refs": seeds,
        "bounds": {
            "max_depth": 0,
            "allowed_resource_types": [],
            "max_targets": case["max_targets"],
            "max_packet_bytes": case["max_packet_bytes"],
            "outcomes": sorted(outcomes),
        },
    }


def project_traversal(case: dict[str, Any]) -> dict[str, Any]:
    """Fetch unique resources once, then enumerate replayable bounded paths."""

    index = visible_index(case)
    seeds, seed_outcomes = authorized_seed_refs(case, index)
    allowed_types = set(case["vocabulary_allowed_resource_types"])
    max_depth = int(case["max_depth"])
    max_targets = int(case["max_targets"])
    included: dict[str, dict[str, Any]] = {}
    patient = index.get(case["patient_ref"])
    if patient is not None:
        included[case["patient_ref"]] = patient
    for seed_ref in seeds:
        included[seed_ref] = index[seed_ref]

    queue: deque[tuple[str, int]] = deque((seed_ref, 0) for seed_ref in seeds)
    expanded: set[str] = set()
    fetched_targets: set[str] = set()
    edges: dict[bytes, dict[str, Any]] = {}
    outcomes = set(seed_outcomes)
    if case["principal"]["purpose"] not in case["allowed_purposes"]:
        outcomes.add("purpose_denial")

    while queue:
        source_ref, depth = queue.popleft()
        if source_ref in expanded or depth >= max_depth:
            continue
        expanded.add(source_ref)
        for pointer, requested_reference in iter_explicit_references(index[source_ref]):
            parsed = parse_relative_reference(requested_reference)
            if (
                parsed is None
                or parsed[0] not in allowed_types
                or not _registered_relation(
                    source_ref.split("/", 1)[0], pointer, parsed[0]
                )
            ):
                continue
            if len(edges) >= MAX_DISCOVERED_EDGES:
                outcomes.add("edge_limit")
                queue.clear()
                break
            resolved = _resolve(index, requested_reference)
            target_type = parsed[0]
            if resolved is None:
                edge = {
                    "source": source_ref,
                    "json_pointer": pointer,
                    "requested_reference": requested_reference,
                    "resolved_reference": None,
                    "target_type": target_type,
                    "state": "unavailable",
                }
            elif not _target_is_patient_authorized(
                resolved[0], resolved[1], case["patient_ref"]
            ):
                outcomes.add("cross_patient_target")
                edge = {
                    "source": source_ref,
                    "json_pointer": pointer,
                    "requested_reference": requested_reference,
                    "resolved_reference": None,
                    "target_type": target_type,
                    "state": "unavailable",
                }
            else:
                target_ref, target, _ = resolved
                if target_ref not in included and len(fetched_targets) >= max_targets:
                    outcomes.add("target_limit")
                    edge = {
                        "source": source_ref,
                        "json_pointer": pointer,
                        "requested_reference": requested_reference,
                        "resolved_reference": None,
                        "target_type": target_type,
                        "state": "unavailable",
                    }
                else:
                    if target_ref not in included:
                        included[target_ref] = target
                        fetched_targets.add(target_ref)
                    edge = {
                        "source": source_ref,
                        "json_pointer": pointer,
                        "requested_reference": requested_reference,
                        "resolved_reference": target_ref,
                        "target_type": target_type,
                        "state": "available",
                    }
                    if depth + 1 < max_depth and target_ref not in expanded:
                        queue.append((target_ref, depth + 1))
            edge_key = canonical_bytes(edge)
            edges[edge_key] = edge

    edge_rows = [edges[key] for key in sorted(edges)]
    model_citations, audit_citations, path_limit_reached = _path_citations(
        seeds, edge_rows, max_depth
    )
    if path_limit_reached:
        outcomes.add("path_citation_limit")
    allowed_locations = {
        (edge["source"], edge["json_pointer"])
        for edge in edge_rows
        if edge["state"] == "available"
    }
    return {
        "resources": _redacted_resources(included, index, allowed_locations),
        "path_citations": model_citations,
        "audit_path_citations": audit_citations,
        "root_refs": seeds,
        "bounds": {
            "max_depth": max_depth,
            "allowed_resource_types": sorted(allowed_types),
            "max_targets": max_targets,
            "max_packet_bytes": case["max_packet_bytes"],
            "max_discovered_edges": MAX_DISCOVERED_EDGES,
            "max_path_citations": MAX_PATH_CITATIONS,
            "outcomes": sorted(outcomes),
        },
    }


def apply_packet_byte_bound(
    case: dict[str, Any], model_packet: dict[str, Any], *, arm: str
) -> tuple[dict[str, Any], list[str]]:
    """Apply the same fail-closed model-context bound to every A11 arm."""

    max_packet_bytes = int(case["max_packet_bytes"])
    if len(canonical_bytes(model_packet)) <= max_packet_bytes:
        return model_packet, []
    bounded_packet = {
        "resources": [],
        "path_citations": [],
        "answerability_receipt": {
            "state": "insufficient",
        },
    }
    if len(canonical_bytes(bounded_packet)) > max_packet_bytes:
        raise ValueError(
            f"max_packet_bytes cannot hold the fail-closed receipt: {case['case_id']}"
        )
    return bounded_packet, ["packet_byte_limit"]
