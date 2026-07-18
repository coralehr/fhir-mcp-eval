from __future__ import annotations

import json
from pathlib import Path

from a11_path_required_benchmark import (
    ARM_STAR,
    ARM_TRAVERSAL,
    ARM_VOCAB_TRAVERSAL,
    compile_case,
    load_fixture,
    run_benchmark,
    write_artifacts,
)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "a11_path_required_cases.json"


def test_registered_answerable_cases_require_a_two_hop_path() -> None:
    fixture = load_fixture(FIXTURE)
    answerable = [case for case in fixture["cases"] if case["answerable"]]

    assert len(answerable) >= 4
    for case in answerable:
        star = compile_case(case, ARM_STAR)
        traversal = compile_case(case, ARM_TRAVERSAL)

        assert not star["mechanism_success"], case["case_id"]
        assert traversal["mechanism_success"], case["case_id"]
        evidence_citations = [
            citation
            for citation in traversal["packet"]["path_citations"]
            if citation["target"] in case["expected_evidence_refs"]
        ]
        assert evidence_citations
        assert all(len(citation["steps"]) >= 2 for citation in evidence_citations)


def test_vocabulary_is_an_isolated_traversal_filter() -> None:
    fixture = load_fixture(FIXTURE)
    strict_byte_win = False

    for case in fixture["cases"]:
        traversal = compile_case(case, ARM_TRAVERSAL)
        vocab = compile_case(case, ARM_VOCAB_TRAVERSAL)

        assert vocab["mechanism_success"] == traversal["mechanism_success"]
        assert vocab["evidence_recall"] == traversal["evidence_recall"]
        assert vocab["packet_bytes"] <= traversal["packet_bytes"]
        strict_byte_win |= vocab["packet_bytes"] < traversal["packet_bytes"]

    assert strict_byte_win


def test_authorization_stale_and_deleted_targets_fail_closed_without_an_oracle() -> None:
    fixture = load_fixture(FIXTURE)
    unavailable_cases = {
        case["failure_mode"]: case
        for case in fixture["cases"]
        if not case["answerable"]
    }

    assert {"cross_practice", "stale_version", "deleted_target"} <= unavailable_cases.keys()

    unavailable_shapes = []
    for failure_mode in ("cross_practice", "stale_version", "deleted_target"):
        case = unavailable_cases[failure_mode]
        result = compile_case(case, ARM_TRAVERSAL)
        refs = {resource["resourceType"] + "/" + resource["id"] for resource in result["packet"]["resources"]}

        assert result["mechanism_success"]
        assert not (refs & set(case["forbidden_resource_refs"]))
        assert result["authorization_leakage_count"] == 0
        assert "MUST NOT" not in json.dumps(result["packet"])
        unavailable = [
            {
                "state": citation["state"],
                "step_count": len(citation["steps"]),
            }
            for citation in result["packet"]["path_citations"]
            if citation["state"] == "unavailable"
        ]
        assert unavailable
        unavailable_shapes.append(unavailable)

    # A caller cannot distinguish cross-practice, stale, and deleted targets from
    # the packet-level state. All three collapse to the same unavailable shape.
    assert unavailable_shapes[0] == unavailable_shapes[1] == unavailable_shapes[2]


def test_purpose_and_registered_bounds_fail_closed() -> None:
    fixture = load_fixture(FIXTURE)
    cases = {case["failure_mode"]: case for case in fixture["cases"]}

    assert {"purpose_denial", "target_limit", "packet_byte_limit"} <= cases.keys()
    for failure_mode in ("purpose_denial", "target_limit", "packet_byte_limit"):
        case = cases[failure_mode]
        result = compile_case(case, ARM_TRAVERSAL)
        packet = result["packet"]
        refs = {
            resource["resourceType"] + "/" + resource["id"]
            for resource in packet["resources"]
        }

        assert result["mechanism_success"]
        assert not (refs & set(case["forbidden_resource_refs"]))
        assert failure_mode in packet["bounds"]["outcomes"]
        assert result["packet_bytes"] <= case["max_packet_bytes"]

    assert not compile_case(cases["purpose_denial"], ARM_TRAVERSAL)[
        "purpose_scope_allowed"
    ]


def test_benchmark_and_artifacts_are_byte_deterministic(tmp_path: Path) -> None:
    fixture = load_fixture(FIXTURE)
    first = run_benchmark(fixture)
    second = run_benchmark(fixture)

    assert first == second

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    manifest_a = write_artifacts(fixture, out_a)
    manifest_b = write_artifacts(fixture, out_b)

    assert manifest_a == manifest_b
    for name in manifest_a["artifacts"]:
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes()


def test_fixture_rejects_duplicate_resource_keys(tmp_path: Path) -> None:
    raw = json.loads(FIXTURE.read_text())
    raw["cases"][0]["resources"].append(raw["cases"][0]["resources"][0])
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw))

    try:
        load_fixture(path)
    except ValueError as exc:
        assert "duplicate resource key" in str(exc)
    else:
        raise AssertionError("duplicate resource key was accepted")
