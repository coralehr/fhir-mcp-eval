import argparse
import contextlib
import io
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import a6_packet_builder as a6
import codex_harness


def _obs(i, day, code="Heart Rate", value=None, unit="bpm"):
    r = {
        "resourceType": "Observation",
        "id": f"o{i}",
        "code": {"text": code},
        "effectiveDateTime": f"2100-01-{day:02d}T00:00:00Z",
    }
    if value is not None:
        r["valueQuantity"] = {"value": value, "unit": unit}
    return r


class IncludePinningTests(unittest.TestCase):
    def test_evicted_medication_target_is_pinned(self):
        med = {"resourceType": "Medication", "id": "m1", "code": {"text": "Heparin"}}
        req = {
            "resourceType": "MedicationRequest",
            "id": "r1",
            "medicationReference": {"reference": "Medication/m1"},
            "authoredOn": "2100-02-01",
        }
        kept = [req]
        universe = [req, med]
        out, pinned = a6.pin_reference_targets(kept, universe)
        self.assertEqual(pinned, 1)
        self.assertTrue(any(r.get("id") == "m1" for r in out))

    def test_already_kept_and_unfetched_targets_not_duplicated(self):
        med = {"resourceType": "Medication", "id": "m1"}
        req = {"resourceType": "MedicationRequest", "id": "r1", "medicationReference": {"reference": "Medication/m1"}}
        req2 = {"resourceType": "MedicationRequest", "id": "r2", "medicationReference": {"reference": "Medication/ghost"}}
        out, pinned = a6.pin_reference_targets([req, med, req2], [req, med, req2])
        self.assertEqual(pinned, 0)
        self.assertEqual(len(out), 3)

    def test_non_pinnable_types_ignored(self):
        enc = {"resourceType": "Encounter", "id": "e1"}
        obs = {"resourceType": "Observation", "id": "o1", "encounter": {"reference": "Encounter/e1"}}
        out, pinned = a6.pin_reference_targets([obs], [obs, enc])
        self.assertEqual(pinned, 0)


class EndpointReserveTests(unittest.TestCase):
    def test_extremes_survive_budget_pressure_from_noisy_type(self):
        # one huge Observation each ~big chars + a tiny Encounter pair at extremes
        noisy = [dict(_obs(i, (i % 27) + 1), padding="x" * 800) for i in range(60)]
        enc_first = {"resourceType": "Encounter", "id": "e_first", "period": {"start": "2100-01-01"}}
        enc_last = {"resourceType": "Encounter", "id": "e_last", "period": {"start": "2100-12-31"}}
        resources = noisy + [enc_first, enc_last]
        kept, stats = a6.bound_resources(
            resources,
            temporal_policy="first_last",
            max_total_resources=200,
            max_packet_chars=9_000,
            endpoint_reserve=True,
        )
        ids = {r["id"] for r in kept}
        self.assertIn("e_first", ids)
        self.assertIn("e_last", ids)

    def test_off_by_default_behavior_unchanged(self):
        resources = [_obs(i, (i % 27) + 1) for i in range(30)]
        kept_off, _ = a6.bound_resources(
            resources, temporal_policy="recent", max_total_resources=10, max_packet_chars=1_000_000
        )
        self.assertEqual(len(kept_off), 10)


class AggSummaryTests(unittest.TestCase):
    def test_counts_extremes_and_unit_guarded_sum(self):
        resources = (
            [_obs(i, i + 1, code="Foley", value=100.0, unit="mL") for i in range(20)]
            + [_obs(100 + i, i + 1, code="Heart Rate", value=80 + i, unit="bpm") for i in range(5)]
        )
        s = a6.aggregate_summary(resources)
        foley = next(r for r in s["code_series"] if r["code"] == "foley")
        self.assertEqual(foley["resource_count"], 20)
        self.assertEqual(foley["value_sum"], 2000.0)
        self.assertEqual(foley["first"], "2100-01-01T00:00:00Z")
        self.assertEqual(foley["last"], "2100-01-20T00:00:00Z")

    def test_mixed_units_suppress_sum(self):
        resources = [
            _obs(1, 1, code="Weight", value=80.0, unit="kg"),
            _obs(2, 2, code="Weight", value=176.0, unit="lb"),
        ]
        s = a6.aggregate_summary(resources)
        w = next(r for r in s["code_series"] if r["code"] == "weight")
        self.assertNotIn("value_sum", w)

    def test_medication_distinct_semantics(self):
        med = {"resourceType": "Medication", "id": "m1", "code": {"text": "Heparin"}}
        reqs = [
            {"resourceType": "MedicationRequest", "id": f"r{i}", "medicationReference": {"reference": "Medication/m1"}, "authoredOn": f"2100-01-{i+1:02d}"}
            for i in range(3)
        ] + [
            {"resourceType": "MedicationRequest", "id": "r9", "medicationCodeableConcept": {"text": "Aspirin"}, "authoredOn": "2100-02-01"}
        ]
        s = a6.aggregate_summary(reqs + [med])
        self.assertEqual(s["medication_distinct_count"], 2)
        self.assertEqual(s["medication_displays"]["heparin"], 3)

    def test_char_cap_truncates(self):
        resources = [_obs(i, (i % 27) + 1, code=f"Lab {i}") for i in range(3000)]
        s = a6.aggregate_summary(resources, max_chars=4_000)
        self.assertTrue(s["truncated"])
        self.assertLessEqual(len(a6._json(s)), 4_200)


class FeatureIsolationTests(unittest.TestCase):
    ROW = {
        "question_id": "q1", "split": "test",
        "question": "what was the last heart rate value of patient 1?",
        "assumption": "", "patient_fhir_id": "p1",
    }

    def test_no_features_packet_matches_base_shape(self):
        rec = a6.build_packet_record(self.ROW, plan_only=True, resources_by_query={}, planner="question-only")
        self.assertEqual(rec["packet"]["features"], [])
        self.assertIsNone(rec["packet"]["aggregate_summary"])
        self.assertEqual(rec["packet"]["pinned_reference_targets"], 0)

    def test_features_recorded_in_packet(self):
        rec = a6.build_packet_record(
            self.ROW, plan_only=True, resources_by_query={}, planner="question-only",
            features={"agg-summary"},
        )
        self.assertEqual(rec["packet"]["features"], ["agg-summary"])

    def test_unregistered_feature_mix_is_rejected_programmatically(self):
        with self.assertRaisesRegex(ValueError, "registered QT arm"):
            a6.build_packet_record(
                self.ROW,
                plan_only=True,
                resources_by_query={},
                planner="question-only",
                features={"agg-summary", "micro-vocab"},
            )

    def test_qt_features_require_question_only_qo_v2(self):
        with self.assertRaisesRegex(ValueError, "question-only qo-v2"):
            a6.build_packet_record(
                self.ROW,
                plan_only=True,
                resources_by_query={},
                planner="metadata-oracle",
                features={"micro-vocab"},
            )

    def test_cli_uses_the_same_qt_validation(self):
        argv = [
            "a6_packet_builder.py",
            "--plan-only",
            "--planner",
            "metadata-oracle",
            "--features",
            "micro-vocab",
        ]
        with (
            mock.patch("sys.argv", argv),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            a6.main()
        self.assertEqual(raised.exception.code, 2)

    def test_qt_packet_build_defaults_to_frozen_a6a_root_bounds(self):
        plan = [{"resource_type": "Observation", "path": "Observation?patient=p1", "reason": "test"}]
        record = a6.build_packet_record(
            self.ROW,
            plan_only=False,
            resources_by_query={plan[0]["path"]: [_obs(1, 1)]},
            planner="question-only",
            plan=plan,
            features={"agg-summary"},
        )

        self.assertEqual(record["packet"]["bounds"]["max_total_resources"], 200)
        self.assertEqual(record["packet"]["bounds"]["max_packet_chars"], 160_000)

    def test_qt_packet_build_rejects_non_frozen_root_bounds(self):
        with self.assertRaisesRegex(ValueError, "frozen A6a root bounds"):
            a6.build_packet_record(
                self.ROW,
                plan_only=True,
                resources_by_query={},
                planner="question-only",
                features={"agg-summary"},
                max_total_resources=120,
                max_packet_chars=100_000,
            )

    def test_cli_rejects_non_frozen_root_bounds_before_live_fetch(self):
        argv = [
            "a6_packet_builder.py",
            "--planner",
            "question-only",
            "--features",
            "agg-summary",
            "--max-total-resources",
            "120",
        ]
        with (
            mock.patch("sys.argv", argv),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            a6.main()
        self.assertEqual(raised.exception.code, 2)


class MicroVocabularyTests(unittest.TestCase):
    ROW = {
        "question_id": "micro-1",
        "split": "test",
        "question": "What organism was found in the last urine microbiology test?",
        "assumption": "",
        "patient_fhir_id": "p1",
    }

    def test_fixed_vocabulary_replaces_free_text_for_microbiology_questions(self):
        self.assertTrue(a6.is_microbiology_question("When was the last stool microbial test?"))
        intent = a6.qo_infer_intent(self.ROW)

        plan = a6.build_search_plan(
            self.ROW,
            intent,
            count=25,
            features={"micro-vocab"},
        )
        traversal_plan = a6.build_search_plan(
            self.ROW,
            intent,
            count=25,
            features={"micro-vocab", "micro-traversal"},
        )

        code_terms = {
            path.split("code:text=", 1)[1]
            for item in plan
            if "code:text=" in (path := item["path"])
        }
        self.assertEqual(code_terms, {"culture", "gram%20stain", "screen", "smear"})
        self.assertTrue(all("organism" not in item["path"] for item in plan))
        self.assertTrue(all(item["relaxation_policy"] == "none" for item in plan))
        self.assertEqual(plan, traversal_plan)

    def test_fixed_vocabulary_misses_do_not_relax_to_bare_observation_search(self):
        class EmptyClient:
            def __init__(self):
                self.paths = []

            def search_with_pagination(self, path, *, max_results=None):
                self.paths.append(path)
                return []

        client = EmptyClient()
        plan = [{
            "resource_type": "Observation",
            "path": "Observation?patient=p1&code:text=culture",
            "reason": "fixed vocabulary",
            "relaxation_policy": "none",
        }]

        a6.fetch_resources(plan, client=client)

        self.assertEqual(client.paths, [plan[0]["path"]])

    def test_manifest_freezes_dispatcher_terms_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.csv"
            output_path = root / "packets.jsonl"
            manifest_path = root / "manifest.json"
            input_path.write_text("question_id,question\nq1,microbiology test\n", encoding="utf-8")
            output_path.write_text("{}\n", encoding="utf-8")
            args = argparse.Namespace(
                limit=1,
                count=25,
                plan_only=True,
                split="test",
                planner="question-only",
                features="micro-vocab,micro-traversal",
                max_total_resources=a6.A6A_MAX_TOTAL_RESOURCES,
                max_packet_chars=a6.A6A_MAX_PACKET_CHARS,
            )
            a6.write_manifest(
                manifest_path,
                input_path=input_path,
                output_path=output_path,
                args=args,
                records=[{"question_id": "q1", "packet": {"sha256": "abc"}}],
            )
            config = json.loads(manifest_path.read_text(encoding="utf-8"))["config"]

        self.assertEqual(config["micro_dispatcher"]["version"], "micro-dispatch-v1")
        self.assertEqual(config["micro_dispatcher"]["question_terms"], list(a6.MICRO_QUESTION_TERMS))
        self.assertEqual(config["max_total_resources"], a6.A6A_MAX_TOTAL_RESOURCES)
        self.assertEqual(config["max_packet_chars"], a6.A6A_MAX_PACKET_CHARS)

    def test_non_microbiology_qt4_features_are_a_prompt_noop(self):
        row = {
            "question_id": "non-micro-1",
            "split": "test",
            "question": "What was the last heart rate?",
            "assumption": "",
            "patient_fhir_id": "p1",
        }
        intent = a6.qo_infer_intent(row)
        base_plan = a6.build_search_plan(row, intent, count=25)
        qt4_plan = a6.build_search_plan(
            row, intent, count=25, features={"micro-vocab", "micro-traversal"}
        )
        resources = {
            base_plan[0]["path"]: [
                {"resourceType": "Observation", "id": "heart-1", "valueQuantity": {"value": 80}}
            ]
        }
        base = a6.build_packet_record(
            row,
            plan_only=False,
            resources_by_query=resources,
            planner="question-only",
            max_total_resources=a6.A6A_MAX_TOTAL_RESOURCES,
            max_packet_chars=a6.A6A_MAX_PACKET_CHARS,
            plan=base_plan,
        )
        qt4 = a6.build_packet_record(
            row,
            plan_only=False,
            resources_by_query=resources,
            planner="question-only",
            max_total_resources=a6.A6A_MAX_TOTAL_RESOURCES,
            max_packet_chars=a6.A6A_MAX_PACKET_CHARS,
            plan=qt4_plan,
            features={"micro-vocab", "micro-traversal"},
        )

        self.assertEqual(base_plan, qt4_plan)
        self.assertEqual(
            codex_harness.build_prompt(base, mode="packet"),
            codex_harness.build_prompt(qt4, mode="packet"),
        )
        self.assertEqual(base["packet"], qt4["packet"])
        packet_without_hash = {
            key: value for key, value in qt4["packet"].items() if key != "sha256"
        }
        self.assertEqual(
            qt4["packet"]["sha256"],
            a6.sha256_text(a6._json(packet_without_hash)),
        )


class MicroTraversalTests(unittest.TestCase):
    def test_exact_reference_traversal_is_deterministic_and_emits_path_receipts(self):
        parent = {
            "resourceType": "Observation",
            "id": "test-1",
            "hasMember": [{"reference": "Observation/org-1"}],
            "specimen": {"reference": "Specimen/spec-1"},
        }
        unrelated = {"resourceType": "Patient", "id": "p1"}
        store = {
            "Observation/org-1": {
                "resourceType": "Observation",
                "id": "org-1",
                "valueString": "E. coli",
            },
            "Specimen/spec-1": {
                "resourceType": "Specimen",
                "id": "spec-1",
                "type": {"text": "Urine"},
            },
        }

        def fetch_by_ids(resource_type, ids):
            return [store[f"{resource_type}/{rid}"] for rid in reversed(ids) if f"{resource_type}/{rid}" in store]

        first = a6.traverse_exact_references([unrelated, parent], fetch_by_ids=fetch_by_ids)
        second = a6.traverse_exact_references([parent, unrelated], fetch_by_ids=fetch_by_ids)

        self.assertEqual(first, second)
        self.assertEqual(
            [f"{r['resourceType']}/{r['id']}" for r in first["resources"]],
            ["Observation/org-1", "Specimen/spec-1"],
        )
        self.assertEqual(
            first["path_receipts"],
            [
                {
                    "depth": 1,
                    "from": "Observation/test-1",
                    "path": "Observation.hasMember[0].reference",
                    "to": "Observation/org-1",
                    "status": "fetched",
                },
                {
                    "depth": 1,
                    "from": "Observation/test-1",
                    "path": "Observation.specimen.reference",
                    "to": "Specimen/spec-1",
                    "status": "fetched",
                },
            ],
        )

    def test_missing_duplicates_and_cycles_are_bounded_without_duplicate_resources(self):
        parent = {
            "resourceType": "Observation",
            "id": "test-1",
            "hasMember": [
                {"reference": "Observation/org-1"},
                {"reference": "Observation/org-1"},
                {"reference": "Observation/missing"},
            ],
        }
        child = {
            "resourceType": "Observation",
            "id": "org-1",
            "hasMember": [{"reference": "Observation/test-1"}],
        }

        result = a6.traverse_exact_references(
            [parent],
            fetch_by_ids=lambda resource_type, ids: [child] if "org-1" in ids else [],
        )

        self.assertEqual([r["id"] for r in result["resources"]], ["org-1"])
        self.assertEqual(result["stats"]["fetch_attempt_count"], 2)
        self.assertEqual(
            [receipt["status"] for receipt in result["path_receipts"]],
            ["fetched", "already_present", "missing", "already_present"],
        )

    def test_invalid_fhir_reference_ids_are_never_queried(self):
        parent = {
            "resourceType": "Observation",
            "id": "test-1",
            "hasMember": [
                {"reference": "Observation/good-1"},
                {"reference": "Observation/bad id"},
                {"reference": "Observation/bad_id"},
                {"reference": f"Observation/{'x' * 65}"},
            ],
        }
        requested = []

        def fetch_by_ids(resource_type, ids):
            requested.extend(ids)
            return [{"resourceType": "Observation", "id": "good-1"}]

        result = a6.traverse_exact_references([parent], fetch_by_ids=fetch_by_ids)

        self.assertEqual(requested, ["good-1"])
        self.assertEqual([r["id"] for r in result["resources"]], ["good-1"])

    def test_resource_and_serialized_byte_bounds_are_hard(self):
        parent = {
            "resourceType": "Observation",
            "id": "test-1",
            "hasMember": [
                {"reference": "Observation/org-1"},
                {"reference": "Observation/org-2"},
            ],
        }
        store = {
            "Observation/org-1": {"resourceType": "Observation", "id": "org-1", "valueString": "small"},
            "Observation/org-2": {"resourceType": "Observation", "id": "org-2", "valueString": "x" * 500},
        }

        def fetch_by_ids(resource_type, ids):
            return [store[f"{resource_type}/{rid}"] for rid in ids]

        resource_limited = a6.traverse_exact_references(
            [parent], fetch_by_ids=fetch_by_ids, max_resources=1
        )
        byte_limited = a6.traverse_exact_references(
            [parent], fetch_by_ids=fetch_by_ids, max_resources=2, max_serialized_bytes=100
        )

        self.assertEqual([r["id"] for r in resource_limited["resources"]], ["org-1"])
        self.assertEqual(
            [r["status"] for r in resource_limited["path_receipts"]],
            ["fetched", "max_resources"],
        )
        self.assertEqual([r["id"] for r in byte_limited["resources"]], ["org-1"])
        self.assertEqual(byte_limited["path_receipts"][1]["status"], "max_serialized_bytes")
        self.assertLessEqual(byte_limited["stats"]["added_serialized_bytes"], 100)

    def test_path_receipt_count_and_bytes_are_hard_bounded(self):
        parent = {
            "resourceType": "Observation",
            "id": "test-1",
            "hasMember": [
                {"reference": f"Observation/child-{index:03d}"}
                for index in range(100)
            ],
        }

        result = a6.traverse_exact_references(
            [parent],
            fetch_by_ids=lambda resource_type, ids: [],
            max_resources=1,
            max_path_receipts=3,
            max_path_receipt_bytes=500,
        )

        self.assertLessEqual(len(result["path_receipts"]), 3)
        self.assertLessEqual(len(a6._json(result["path_receipts"]).encode("utf-8")), 500)
        self.assertEqual(result["stats"]["path_receipt_count"], len(result["path_receipts"]))
        self.assertEqual(
            result["stats"]["path_receipt_serialized_bytes"],
            len(a6._json(result["path_receipts"]).encode("utf-8")),
        )
        self.assertEqual(result["stats"]["path_receipts_omitted"], 97)
        self.assertEqual(result["stats"]["path_status_counts"]["missing"], 1)
        self.assertEqual(result["stats"]["path_status_counts"]["max_resources"], 99)
        self.assertEqual(sum(result["stats"]["path_status_counts"].values()), 100)

    def test_receipt_byte_bound_must_fit_the_empty_json_array(self):
        with self.assertRaisesRegex(ValueError, "at least 2"):
            a6.traverse_exact_references(
                [],
                fetch_by_ids=lambda resource_type, ids: [],
                max_path_receipt_bytes=1,
            )

    def test_depth_bound_prevents_fetching_the_next_hop(self):
        parent = {
            "resourceType": "Observation",
            "id": "test-1",
            "hasMember": [{"reference": "Observation/org-1"}],
        }
        child = {
            "resourceType": "Observation",
            "id": "org-1",
            "hasMember": [{"reference": "Observation/susc-1"}],
        }
        grandchild = {"resourceType": "Observation", "id": "susc-1"}
        store = {"org-1": child, "susc-1": grandchild}
        requested = []

        def fetch_by_ids(resource_type, ids):
            requested.extend(ids)
            return [store[resource_id] for resource_id in ids]

        result = a6.traverse_exact_references(
            [parent], fetch_by_ids=fetch_by_ids, max_depth=1
        )

        self.assertEqual(requested, ["org-1"])
        self.assertEqual([r["id"] for r in result["resources"]], ["org-1"])
        self.assertEqual(result["limits"]["max_depth"], 1)

    def test_packet_builder_adds_traversed_resources_and_receipts(self):
        row = {
            "question_id": "micro-1",
            "split": "test",
            "question": "What organism was found in the last urine culture?",
            "assumption": "",
            "patient_fhir_id": "p1",
        }
        plan = [{"resource_type": "Observation", "path": "Observation?patient=p1", "reason": "test"}]
        parent = {
            "resourceType": "Observation",
            "id": "test-1",
            "hasMember": [{"reference": "Observation/org-1"}],
        }
        child = {"resourceType": "Observation", "id": "org-1", "valueString": "E. coli"}

        record = a6.build_packet_record(
            row,
            plan_only=False,
            resources_by_query={plan[0]["path"]: [parent]},
            planner="question-only",
            max_total_resources=a6.A6A_MAX_TOTAL_RESOURCES,
            max_packet_chars=a6.A6A_MAX_PACKET_CHARS,
            plan=plan,
            features={"micro-vocab", "micro-traversal"},
            reference_fetcher=lambda resource_type, ids: [child],
        )

        self.assertEqual(record["packet"]["source_resource_ids"], ["Observation/org-1", "Observation/test-1"])
        self.assertEqual(record["packet"]["resource_count"], 2)
        self.assertEqual(record["packet"]["reference_traversal"]["stats"]["added_resource_count"], 1)
        self.assertEqual(record["packet"]["reference_traversal"]["path_receipts"][0]["to"], "Observation/org-1")

    def test_packet_builder_records_frozen_traversal_contract_with_no_edges(self):
        row = {
            "question_id": "micro-empty",
            "split": "test",
            "question": "What organism was found in the last culture?",
            "assumption": "",
            "patient_fhir_id": "p1",
        }
        plan = [
            {
                "resource_type": "Observation",
                "path": "Observation?patient=p1",
                "reason": "test",
            }
        ]
        root = {"resourceType": "Observation", "id": "test-1", "valueString": "negative"}

        record = a6.build_packet_record(
            row,
            plan_only=False,
            resources_by_query={plan[0]["path"]: [root]},
            planner="question-only",
            max_total_resources=a6.A6A_MAX_TOTAL_RESOURCES,
            max_packet_chars=a6.A6A_MAX_PACKET_CHARS,
            plan=plan,
            features={"micro-vocab", "micro-traversal"},
            reference_fetcher=lambda resource_type, ids: [],
        )

        traversal = record["packet"]["reference_traversal"]
        self.assertEqual(traversal["version"], a6.MICRO_TRAVERSAL_VERSION)
        self.assertEqual(traversal["limits"]["max_depth"], 2)
        self.assertEqual(traversal["stats"]["fetch_attempt_count"], 0)
        self.assertEqual(traversal["stats"]["added_resource_count"], 0)
        self.assertEqual(traversal["path_receipts"], [])


if __name__ == "__main__":
    unittest.main()
