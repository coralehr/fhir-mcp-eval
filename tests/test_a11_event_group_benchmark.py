from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from a11_evidence_core import project_traversal
from a11_event_group_benchmark import (
    ARM_EVENT_GROUP,
    ARM_FLAT_TRAVERSAL,
    ARM_VOCABULARY_STAR,
    compile_case,
    compile_event_groups,
    load_fixture,
    plan_question,
    run_benchmark,
    write_artifacts,
)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "a11_event_group_cases.json"


class A11EventGroupBenchmarkTests(unittest.TestCase):
    def test_three_arms_isolate_retrieval_from_grouping(self) -> None:
        fixture = load_fixture(FIXTURE)

        for case in fixture["cases"]:
            star = compile_case(case, ARM_VOCABULARY_STAR)
            flat = compile_case(case, ARM_FLAT_TRAVERSAL)
            grouped = compile_case(case, ARM_EVENT_GROUP)

            if case["answerable"]:
                self.assertLess(star["evidence_recall"], 1.0, case["case_id"])
                self.assertEqual(flat["evidence_recall"], 1.0, case["case_id"])
                self.assertEqual(grouped["evidence_recall"], 1.0, case["case_id"])
                self.assertTrue(grouped["mechanism_success"], case["case_id"])

            self.assertNotIn("event_groups", flat["model_packet"])
            self.assertIn("event_groups", grouped["model_packet"])
            self.assertEqual(flat["retrieval_receipt"], grouped["retrieval_receipt"])

    def test_event_group_is_a_pure_transform_of_the_t_retrieval_source(self) -> None:
        fixture = load_fixture(FIXTURE)
        case = copy.deepcopy(next(item for item in fixture["cases"] if item["answerable"]))
        source_packet = project_traversal(case)
        plan = plan_question(case["question"])
        original = compile_event_groups(source_packet, plan)

        case["seed_refs"] = ["Observation/gold-selected-root"]
        case["expected_event_root"] = "Observation/gold-selected-root"
        self.assertEqual(original, compile_event_groups(source_packet, plan))

    def test_event_groups_have_canonical_time_rank_and_replayable_edges(self) -> None:
        fixture = load_fixture(FIXTURE)
        for case in fixture["cases"]:
            result = compile_case(case, ARM_EVENT_GROUP)
            groups = result["model_packet"]["event_groups"]
            if case["answerable"]:
                self.assertGreaterEqual(len(groups), 1)
                self.assertEqual(
                    [group["temporal_rank"]["ordinal"] for group in groups],
                    list(range(1, len(groups) + 1)),
                )
                selected = [
                    group
                    for group in groups
                    if group["temporal_rank"]["selected_for_question"]
                ]
                self.assertEqual(len(selected), 1)
                self.assertEqual(
                    selected[0]["root"]["reference"], case["expected_event_root"]
                )

            source_resources = {
                f"{entry['resource']['resourceType']}/{entry['resource']['id']}": entry["resource"]
                for entry in case["resources"]
                if entry["practice_id"] == case["principal"]["practice_id"]
            }
            for group in groups:
                for edge in group["typed_edges"]:
                    if edge["state"] == "unavailable":
                        self.assertIsNone(edge["target"])
                        continue
                    source = source_resources[edge["source"]]
                    pointer = edge["json_pointer"].strip("/").split("/")
                    value: object = source
                    for segment in pointer:
                        value = value[int(segment)] if isinstance(value, list) else value[segment]
                    self.assertEqual(
                        value["reference"], edge["requested_reference"]
                    )
                    self.assertTrue(
                        edge["requested_reference"] == edge["target"]
                        or edge["requested_reference"].startswith(
                            f"{edge['target']}/_history/"
                        )
                    )

    def test_answerability_receipt_contains_requirements_not_gold_or_answers(self) -> None:
        fixture = load_fixture(FIXTURE)

        for case in fixture["cases"]:
            grouped = compile_case(case, ARM_EVENT_GROUP)
            serialized = json.dumps(grouped["model_packet"]["answerability_receipt"], sort_keys=True).lower()

            self.assertNotIn("expected", serialized)
            self.assertNotIn("gold", serialized)
            self.assertNotIn("reference_answer", serialized)
            self.assertNotIn(case["reference_answer"].lower(), serialized)
            expected_state = "sufficient" if case["answerable"] else "insufficient"
            self.assertEqual(grouped["model_packet"]["answerability_receipt"]["state"], expected_state)

    def test_unanswerable_missing_path_fails_closed(self) -> None:
        fixture = load_fixture(FIXTURE)
        case = next(item for item in fixture["cases"] if not item["answerable"])
        grouped = compile_case(case, ARM_EVENT_GROUP)

        self.assertTrue(grouped["mechanism_success"])
        self.assertEqual(grouped["model_packet"]["answerability_receipt"]["state"], "insufficient")
        self.assertEqual(grouped["authorization_leakage_count"], 0)

    def test_scope_version_and_purpose_cases_remain_opaque_in_every_arm(self) -> None:
        fixture = load_fixture(FIXTURE)
        failure_modes = {
            "cross_practice",
            "stale_version",
            "purpose_denial",
            "cross_patient_seed",
            "cross_patient_terminal",
        }

        for case in fixture["cases"]:
            if case.get("failure_mode") not in failure_modes:
                continue
            for arm in (ARM_VOCABULARY_STAR, ARM_FLAT_TRAVERSAL, ARM_EVENT_GROUP):
                result = compile_case(case, arm)
                serialized = json.dumps(result["model_packet"], sort_keys=True)
                self.assertEqual(result["authorization_leakage_count"], 0)
                for forbidden in case["forbidden_resource_refs"]:
                    self.assertNotIn(forbidden, serialized)
                self.assertNotIn("SECRET CROSS-PRACTICE", serialized)
                self.assertNotIn("SECRET-CROSS-PRACTICE", serialized)
                self.assertNotIn("SECRET EXTENSION", serialized)
                self.assertNotIn("SECRET OTHER-PATIENT", serialized)

    def test_gold_audit_fields_cannot_change_model_packet(self) -> None:
        fixture = load_fixture(FIXTURE)
        case = next(item for item in fixture["cases"] if item["answerable"])

        for arm in (ARM_VOCABULARY_STAR, ARM_FLAT_TRAVERSAL, ARM_EVENT_GROUP):
            original = compile_case(case, arm)["model_packet"]
            mutated = copy.deepcopy(case)
            mutated["reference_answer"] = "A different hidden answer"
            mutated["expected_event_root"] = "Observation/not-a-real-root"
            mutated["expected_evidence_refs"] = ["Observation/not-real"]
            mutated["answerable"] = not case["answerable"]
            mutated["failure_mode"] = "counterfactual"
            mutated["sealed_question_plan"] = {"malicious": "gold-derived"}
            mutated["question_family"] = "gold-family"
            mutated["temporal_policy"] = "gold-policy"
            mutated["required_path_signatures"] = [["Gold.secret", "Gold.answer"]]
            self.assertEqual(original, compile_case(mutated, arm)["model_packet"])

    def test_sealed_question_plans_are_derived_from_question_only(self) -> None:
        fixture = load_fixture(FIXTURE)
        for case in fixture["cases"]:
            self.assertEqual(case["sealed_question_plan"], plan_question(case["question"]))

    def test_event_group_packet_byte_bound_fails_closed(self) -> None:
        fixture = load_fixture(FIXTURE)
        template = next(item for item in fixture["cases"] if item["answerable"])
        for arm in (ARM_VOCABULARY_STAR, ARM_FLAT_TRAVERSAL, ARM_EVENT_GROUP):
            original = compile_case(template, arm)
            exact = copy.deepcopy(template)
            exact["max_packet_bytes"] = original["packet_bytes"]
            self.assertEqual(compile_case(exact, arm)["packet_bytes"], original["packet_bytes"])

            above = copy.deepcopy(template)
            above["max_packet_bytes"] = original["packet_bytes"] + 1
            self.assertEqual(compile_case(above, arm)["packet_bytes"], original["packet_bytes"])

            below = copy.deepcopy(template)
            below["max_packet_bytes"] = original["packet_bytes"] - 1
            bounded = compile_case(below, arm)
            self.assertLessEqual(bounded["packet_bytes"], below["max_packet_bytes"])
            self.assertEqual(
                bounded["model_packet"]["answerability_receipt"]["state"],
                "insufficient",
            )
            self.assertIn("packet_byte_limit", bounded["bound_outcomes"])
            serialized = json.dumps(bounded["model_packet"], sort_keys=True)
            self.assertNotIn(arm, serialized)
            self.assertNotIn("packet_byte_limit", serialized)
            self.assertNotIn("bound-receipt", serialized)

    def test_temporal_ties_use_id_order_independent_of_input_order(self) -> None:
        fixture = load_fixture(FIXTURE)
        case = copy.deepcopy(next(item for item in fixture["cases"] if item["answerable"] and item["sealed_question_plan"]["temporal_policy"] == "latest" and len(item["seed_refs"]) >= 2))
        roots = set(case["seed_refs"])
        for entry in case["resources"]:
            reference = f"{entry['resource']['resourceType']}/{entry['resource']['id']}"
            if reference in roots:
                entry["resource"]["effectiveDateTime"] = "2100-01-01T00:00:00Z"

        latest = compile_case(case, ARM_EVENT_GROUP)["model_packet"]
        reversed_case = copy.deepcopy(case)
        reversed_case["seed_refs"].reverse()
        reversed_case["resources"].reverse()
        self.assertEqual(latest, compile_case(reversed_case, ARM_EVENT_GROUP)["model_packet"])
        selected_latest = next(group for group in latest["event_groups"] if group["temporal_rank"]["selected_for_question"])
        self.assertEqual(selected_latest["root"]["reference"], max(roots))

        first_case = copy.deepcopy(case)
        first_case["question"] = first_case["question"].replace("latest", "first")
        first = compile_case(first_case, ARM_EVENT_GROUP)["model_packet"]
        selected_first = next(group for group in first["event_groups"] if group["temporal_rank"]["selected_for_question"])
        self.assertEqual(selected_first["root"]["reference"], min(roots))

    def test_issued_never_controls_clinical_event_ranking(self) -> None:
        fixture = load_fixture(FIXTURE)
        case = copy.deepcopy(next(item for item in fixture["cases"] if item["case_id"] == "a11-latest-culture-organism"))
        roots = set(case["seed_refs"])
        for entry in case["resources"]:
            reference = f"{entry['resource']['resourceType']}/{entry['resource']['id']}"
            if reference in roots:
                entry["resource"]["issued"] = (
                    "2100-12-31T00:00:00Z" if reference.endswith("old") else "2100-01-01T00:00:00Z"
                )
        packet = compile_case(case, ARM_EVENT_GROUP)["model_packet"]
        selected = next(group for group in packet["event_groups"] if group["temporal_rank"]["selected_for_question"])
        self.assertEqual(selected["root"]["reference"], "Observation/culture-root-new")

        missing_time = copy.deepcopy(case)
        root = next(
            entry["resource"]
            for entry in missing_time["resources"]
            if entry["resource"].get("id") == "culture-root-new"
        )
        root.pop("effectiveDateTime")
        denied = compile_case(missing_time, ARM_EVENT_GROUP)["model_packet"]
        self.assertEqual(denied["answerability_receipt"]["state"], "insufficient")
        self.assertEqual(denied["answerability_receipt"]["reason"], "clinical_time_missing")

    def test_exact_version_resolution_and_mixed_stale_edge(self) -> None:
        fixture = load_fixture(FIXTURE)
        case = next(item for item in fixture["cases"] if item["case_id"] == "a11-event-group-exact-version-available")
        flat = compile_case(case, ARM_FLAT_TRAVERSAL)
        grouped = compile_case(case, ARM_EVENT_GROUP)

        self.assertEqual(flat["evidence_recall"], 1.0)
        self.assertTrue(grouped["mechanism_success"])
        serialized = json.dumps(grouped["model_packet"], sort_keys=True)
        self.assertIn("Observation/versioned-terminal/_history/1", serialized)
        self.assertNotIn("Observation/versioned-stale", serialized)

    def test_unique_target_budget_handles_convergence_and_cycle(self) -> None:
        fixture = load_fixture(FIXTURE)
        case = copy.deepcopy(next(item for item in fixture["cases"] if item["case_id"] == "a11-event-group-exact-version-available"))
        case["max_targets"] = 2
        case["max_depth"] = 3
        case["forbidden_resource_refs"] = []
        case["expected_unavailable_refs"] = []
        case["resources"] = [
            entry
            for entry in case["resources"]
            if entry["resource"].get("id") != "versioned-stale"
        ]
        panel = next(entry["resource"] for entry in case["resources"] if entry["resource"].get("id") == "versioned-panel")
        panel["hasMember"] = [
            {"reference": "Observation/versioned-terminal/_history/1"},
            {"reference": "Observation/versioned-terminal/_history/1"},
        ]
        terminal = next(entry["resource"] for entry in case["resources"] if entry["resource"].get("id") == "versioned-terminal")
        terminal["hasMember"] = [{"reference": "Observation/versioned-root"}]

        projection = project_traversal(case)
        refs = {
            f"{resource['resourceType']}/{resource['id']}"
            for resource in projection["resources"]
        }
        self.assertIn("Observation/versioned-terminal", refs)
        self.assertNotIn("target_limit", projection["bounds"]["outcomes"])
        self.assertLess(len(projection["path_citations"]), 20)

    def test_unregistered_reference_relation_is_never_traversed(self) -> None:
        fixture = load_fixture(FIXTURE)
        case = copy.deepcopy(next(item for item in fixture["cases"] if item["case_id"] == "a11-event-group-exact-version-available"))
        root = next(entry["resource"] for entry in case["resources"] if entry["resource"].get("id") == "versioned-root")
        root["derivedFrom"] = [
            {
                "reference": "Observation/unregistered-secret",
                "display": "SECRET UNREGISTERED DISPLAY",
            }
        ]
        case["resources"].append(
            {
                "practice_id": "practice-alpha",
                "resource": {
                    "resourceType": "Observation",
                    "id": "unregistered-secret",
                    "meta": {"versionId": "1"},
                    "subject": {"reference": case["patient_ref"]},
                    "valueString": "SECRET UNREGISTERED VALUE",
                },
            }
        )

        projection = project_traversal(case)
        serialized = json.dumps(projection["resources"], sort_keys=True)
        self.assertNotIn("unregistered-secret", serialized)
        self.assertNotIn("SECRET UNREGISTERED", serialized)
        self.assertTrue(
            all(
                "derivedFrom" not in json.dumps(citation)
                for citation in projection["path_citations"]
            )
        )

    def test_nested_reference_cannot_masquerade_as_registered_relation(self) -> None:
        fixture = load_fixture(FIXTURE)
        case = copy.deepcopy(next(item for item in fixture["cases"] if item["case_id"] == "a11-event-group-exact-version-available"))
        root = next(entry["resource"] for entry in case["resources"] if entry["resource"].get("id") == "versioned-root")
        root["hasMember"][0]["extension"] = [
            {
                "url": "https://invalid.example/nested",
                "valueReference": {
                    "reference": "Observation/nested-secret",
                    "display": "SECRET NESTED DISPLAY",
                },
            }
        ]
        root["hasMember"].append(
            {
                "reference": "Observation/missing-outer",
                "extension": [
                    {
                        "url": "https://invalid.example/nested-missing",
                        "valueReference": {
                            "reference": "Observation/nested-secret"
                        },
                    }
                ],
            }
        )
        case["resources"].append(
            {
                "practice_id": "practice-alpha",
                "resource": {
                    "resourceType": "Observation",
                    "id": "nested-secret",
                    "meta": {"versionId": "1"},
                    "subject": {"reference": case["patient_ref"]},
                    "valueString": "SECRET NESTED VALUE",
                },
            }
        )

        projection = project_traversal(case)
        serialized = json.dumps(projection["resources"], sort_keys=True)
        self.assertNotIn("nested-secret", serialized)
        self.assertNotIn("SECRET NESTED", serialized)
        self.assertTrue(
            all(
                "nested-secret" not in json.dumps(citation)
                for citation in projection["path_citations"]
            )
        )

    def test_edge_and_path_caps_bound_duplicate_and_unavailable_fanout(self) -> None:
        fixture = load_fixture(FIXTURE)
        template = next(item for item in fixture["cases"] if item["case_id"] == "a11-event-group-exact-version-available")

        duplicate = copy.deepcopy(template)
        duplicate["max_packet_bytes"] = 1_000_000
        duplicate["resources"] = [
            entry
            for entry in duplicate["resources"]
            if entry["resource"].get("id") != "versioned-stale"
        ]
        root = next(entry["resource"] for entry in duplicate["resources"] if entry["resource"].get("id") == "versioned-root")
        panel = next(entry["resource"] for entry in duplicate["resources"] if entry["resource"].get("id") == "versioned-panel")
        root["hasMember"] = [
            {"reference": "Observation/versioned-panel/_history/1"}
            for _ in range(50)
        ]
        panel["hasMember"] = [
            {"reference": "Observation/versioned-terminal/_history/1"}
            for _ in range(50)
        ]
        projection = project_traversal(duplicate)
        self.assertIn("path_citation_limit", projection["bounds"]["outcomes"])
        self.assertLessEqual(len(projection["path_citations"]), 256)

        unavailable = copy.deepcopy(template)
        unavailable["max_packet_bytes"] = 1_000_000
        root = next(entry["resource"] for entry in unavailable["resources"] if entry["resource"].get("id") == "versioned-root")
        root["hasMember"] = [
            {"reference": f"Observation/missing-{index}"}
            for index in range(200)
        ]
        projection = project_traversal(unavailable)
        self.assertIn("edge_limit", projection["bounds"]["outcomes"])
        self.assertLessEqual(len(projection["path_citations"]), 128)
        grouped = compile_case(unavailable, ARM_EVENT_GROUP)
        self.assertEqual(
            grouped["model_packet"]["answerability_receipt"]["state"],
            "insufficient",
        )

    def test_target_bound_forces_a_generic_insufficient_receipt(self) -> None:
        fixture = load_fixture(FIXTURE)
        case = copy.deepcopy(next(item for item in fixture["cases"] if item["case_id"] == "a11-event-group-exact-version-available"))
        case["max_targets"] = 1

        grouped = compile_case(case, ARM_EVENT_GROUP)
        self.assertIn("target_limit", grouped["bound_outcomes"])
        self.assertEqual(
            grouped["model_packet"]["answerability_receipt"],
            {
                "version": "a11-answerability-v1",
                "question_plan": plan_question(case["question"]),
                "state": "insufficient",
                "reason": "evidence_incomplete",
                "selected_group_count": 0,
            },
        )

    def test_v_requires_every_expected_terminal_to_be_absent(self) -> None:
        fixture = load_fixture(FIXTURE)
        case = copy.deepcopy(next(item for item in fixture["cases"] if item["case_id"] == "a11-event-group-exact-version-available"))
        case["expected_evidence_refs"] = [
            "Observation/versioned-root",
            "Observation/versioned-terminal",
        ]
        result = compile_case(case, ARM_VOCABULARY_STAR)
        self.assertFalse(result["mechanism_success"])

    def test_fixture_rejects_an_empty_answerable_stratum(self) -> None:
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for case in raw["cases"]:
            case["answerable"] = False
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "all-unanswerable.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no answerable cases"):
                load_fixture(path)

    def test_fixture_rejects_non_list_purpose_and_stale_question_plan(self) -> None:
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        raw["cases"][0]["allowed_purposes"] = "TREATMENT"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad-purpose.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no allowed purposes"):
                load_fixture(path)

        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        raw["cases"][0]["sealed_question_plan"]["question_sha256"] = "stale"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stale-plan.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "question plan is absent or stale"):
                load_fixture(path)

        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        raw["cases"][0]["expected_evidence_refs"] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty-evidence.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no expected evidence"):
                load_fixture(path)

    def test_artifacts_are_byte_deterministic_and_zero_model(self) -> None:
        fixture = load_fixture(FIXTURE)
        self.assertEqual(run_benchmark(fixture), run_benchmark(fixture))

        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = Path(first_dir)
            second = Path(second_dir)
            manifest_a = write_artifacts(fixture, first)
            manifest_b = write_artifacts(fixture, second)

            self.assertEqual(manifest_a, manifest_b)
            self.assertEqual(manifest_a["model_calls"], 0)
            self.assertEqual(
                set(manifest_a["compiler_dependencies"]),
                {"a11_event_group_benchmark.py", "a11_evidence_core.py"},
            )
            for name in manifest_a["artifacts"]:
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
