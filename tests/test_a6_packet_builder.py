import unittest
import json
import tempfile
from pathlib import Path

import a6_packet_builder as a6
from fhir_client import FHIRPaginationError, FHIRSearchError
from medplum_fhir_client import MedplumFHIRClient


class A6PacketBuilderTests(unittest.TestCase):
    def test_frozen_question_spec_filters_and_orders_packet_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.csv"
            input_path.write_text(
                "question_id,split,question\nq1,test,one\nq2,test,two\nq3,valid,three\n",
                encoding="utf-8",
            )
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps({"question_ids": ["q2", "q1"]}), encoding="utf-8"
            )

            rows = a6.load_rows(
                input_path,
                split="test",
                question_ids=a6.load_question_ids(spec_path),
            )

        self.assertEqual([row["question_id"] for row in rows], ["q2", "q1"])
    def test_infers_observation_month_window_from_question_metadata(self):
        row = {
            "question_id": "q1",
            "question": "Did patient 10025463 receive any lab testing in 11/2136?",
            "patient_fhir_id": "Patient/p1",
            "main_table_name": "labevents",
            "assumption": "Assume the current time is 2136-12-31 23:59:00.",
            "val_dict": "{'val_placeholder': {'patient_id': 10025463, 'lab_name': 'mcv'}, 'op_placeholder': {}, 'time_placeholder': {'time_filter_global1': {'nlq': 'in 11/2136', 'type': 'abs-month-in'}}}",
        }

        intent = a6.infer_intent(row)
        self.assertEqual(intent["resource_types"], ["Observation"])
        self.assertEqual(intent["date_windows"][0]["start"], "2136-11-01")
        self.assertEqual(intent["date_windows"][0]["end"], "2136-11-30")
        self.assertIn("mcv", intent["search_terms"])

    def test_keeps_first_and_last_for_temporal_questions(self):
        row = {
            "question_id": "q2",
            "question": "What was the first measured height?",
            "patient_fhir_id": "p2",
            "main_table_name": "chartevents",
            "assumption": "",
            "val_dict": "{'val_placeholder': {'vital_name': 'height'}, 'op_placeholder': {}, 'time_placeholder': {'time_filter_exact1': {'nlq': 'first', 'type': 'exact-first'}}}",
        }

        intent = a6.infer_intent(row)
        plan = a6.build_search_plan(row, intent, count=25)

        self.assertEqual(intent["temporal_policy"], "first_last")
        self.assertTrue(any("_sort=date" in query["path"] for query in plan))
        self.assertTrue(any("_sort=-date" in query["path"] for query in plan))

    def test_previous_year_is_date_window_not_first_last(self):
        row = {
            "question_id": "q-prev",
            "question": "Were there any procedures conducted during the previous year?",
            "patient_fhir_id": "p-prev",
            "main_table_name": "procedures_icd",
            "assumption": "Assume the current time is 2137-12-31 23:59:00.",
            "val_dict": "{'val_placeholder': {'patient_id': 10006580}, 'op_placeholder': {}, 'time_placeholder': {'time_filter_global1': {'nlq': 'last year', 'type': 'rel-year-last'}}}",
        }

        intent = a6.infer_intent(row)
        plan = a6.build_search_plan(row, intent, count=25)

        self.assertEqual(intent["temporal_policy"], "recent")
        self.assertEqual(intent["date_windows"][0]["start"], "2136-01-01")
        self.assertEqual(len(plan), 1)

    def test_root_bounds_never_admit_oversized_first_resource(self):
        kept, stats = a6.bound_resources(
            [{"resourceType": "Observation", "id": "huge", "valueString": "x" * 500}],
            temporal_policy="recent",
            max_total_resources=10,
            max_packet_chars=100,
        )

        self.assertEqual(kept, [])
        self.assertLessEqual(stats["char_count"], 100)
        self.assertTrue(stats["char_budget_hit"])

    def test_mandatory_patient_that_cannot_fit_fails_packet_build(self):
        with self.assertRaisesRegex(ValueError, "Patient resources exceed"):
            a6.bound_resources(
                [{"resourceType": "Patient", "id": "p1", "name": "x" * 500}],
                temporal_policy="recent",
                max_total_resources=1,
                max_packet_chars=100,
            )

    def test_fetch_receipt_is_precise_and_transport_failure_aborts(self):
        precise = "Observation?patient=p1&code:text=heart"
        relaxed = "Observation?patient=p1"

        class Client:
            def search_with_pagination(self, path, *, max_results=None):
                self.max_results = max_results
                if path == precise:
                    return []
                return [
                    {"resourceType": "Observation", "id": "one"},
                    {"resourceType": "Observation", "id": "two"},
                ]

        plan = [{"resource_type": "Observation", "path": precise, "reason": "test"}]
        resources = a6.fetch_resources(plan, per_query_cap=1, client=Client())
        self.assertEqual([item["id"] for item in resources[precise]], ["one"])
        self.assertEqual(
            plan[0]["fetch_receipt"],
            {
                "status": "ok",
                "initial_result_count": 0,
                "relaxation_attempts": [{"path": relaxed, "result_count": 2}],
                "pre_bound_count": 2,
                "retained_count": 1,
                "dropped_count": 1,
            },
        )

        class FailingClient:
            def search_with_pagination(self, _path, *, max_results=None):
                raise FHIRSearchError(400)

        failed_plan = [
            {"resource_type": "Observation", "path": precise, "reason": "test"}
        ]
        with self.assertRaisesRegex(a6.PacketFetchError, "HTTP 400"):
            a6.fetch_resources(failed_plan, client=FailingClient())
        self.assertEqual(failed_plan[0]["fetch_receipt"]["status"], "http_error")

    def test_medplum_http_and_incomplete_pagination_fail_closed(self):
        class Response:
            def __init__(self, status_code, *, next_url=None):
                self.status_code = status_code
                self._next_url = next_url

            def json(self):
                links = (
                    [{"relation": "next", "url": self._next_url}]
                    if self._next_url
                    else []
                )
                return {"entry": [], "link": links}

        class ErrorSession:
            def get(self, _path):
                return Response(429)

        client = MedplumFHIRClient.__new__(MedplumFHIRClient)
        client.session = ErrorSession()
        with self.assertRaises(FHIRSearchError):
            client._fetch_resources_with_pagination("start")

        class EndlessSession:
            def get(self, _path):
                return Response(200, next_url="next")

        client.session = EndlessSession()
        with self.assertRaises(FHIRPaginationError):
            client._fetch_resources_with_pagination("start")

    def test_does_not_emit_gold_fields_in_packet_record(self):
        row = {
            "question_id": "q3",
            "question": "Was a medication prescribed?",
            "patient_fhir_id": "p3",
            "main_table_name": "prescriptions",
            "true_answer": "[[1]]",
            "true_fhir_ids": "{'MedicationRequest': ['secret']}",
            "sql_query": "SELECT secret",
            "val_dict": "{'val_placeholder': {'drug_name': 'glucagon'}, 'op_placeholder': {}, 'time_placeholder': {}}",
        }

        record = a6.build_packet_record(row, plan_only=True, resources_by_query={})

        dumped = str(record)
        self.assertNotIn("true_answer", dumped)
        self.assertNotIn("true_fhir_ids", dumped)
        self.assertNotIn("SELECT secret", dumped)
        self.assertEqual(record["intent"]["resource_types"], ["MedicationRequest"])


class QuestionOnlyPlannerTests(unittest.TestCase):
    def test_holdout_common_planner_is_versioned_as_qo_v2_1(self):
        self.assertEqual(a6.QO_PLANNER_VERSION, "qo-v2.1")

    ROW = {
        "question_id": "q10",
        "split": "valid",
        "question": "what was the last heart rate value of patient 10014729 since 12/2100?",
        "assumption": "assume that the current time is 2100-12-31 23:59:00",
        "patient_fhir_id": "p10",
        # construction metadata a real query never has:
        "main_table_name": "chartevents",
        "val_dict": "{'val_placeholder': {'vital_name': 'heart rate'}, 'time_placeholder': {'t1': {'nlq': 'since 12/2100'}}}",
        "template": "t",
        "true_answer": "[[88]]",
        "true_fhir_ids": "{'Observation': ['secret-id']}",
        "sql_query": "SELECT secret",
        "proc_query": "x",
    }

    def test_question_only_intent_ignores_all_metadata(self):
        stripped = {k: self.ROW[k] for k in a6.QUESTION_ONLY_FIELDS}
        self.assertEqual(a6.qo_infer_intent(self.ROW), a6.qo_infer_intent(stripped))

    def test_question_only_types_terms_window(self):
        intent = a6.qo_infer_intent(self.ROW)
        self.assertEqual(intent["planner"], a6.QO_PLANNER_VERSION)
        self.assertIn("Observation", intent["resource_types"])
        self.assertIn("heart rate", intent["search_terms"])
        self.assertEqual(intent["temporal_policy"], "first_last")
        self.assertEqual(intent["date_windows"][0]["start"], "2100-12-01")

    def test_question_only_type_mapping(self):
        cases = {
            "count the times patient 1 received a heparin prescription": "MedicationRequest",
            "did patient 1 have a ventilation procedure in 2100?": "Procedure",
            "what was the diagnosis of patient 1?": "Condition",
            "when was patient 1 admitted to the icu?": "Encounter",
            "what is the gender of patient 1?": "Patient",
        }
        for question, expected in cases.items():
            self.assertIn(expected, a6.qo_infer_resource_types(question), question)

    def test_question_only_routes_preexisting_planner_repair_language(self):
        cases = {
            "did the patient receive insertion of a peripheral vessel stent treatment?": "Procedure",
            "how many times did the patient have cerebral ventricular #1 since january?": "Observation",
            "when was the first minimum immunoglobulin g value?": "Observation",
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertIn(expected, a6.qo_infer_resource_types(question))

    def test_question_only_disambiguates_prescription_and_procedure_terms(self):
        self.assertEqual(
            a6.qo_infer_resource_types(
                "what total dose of lidocaine for catheter insertions was prescribed?"
            ),
            ["MedicationRequest"],
        )
        self.assertEqual(
            a6.qo_infer_resource_types(
                "when was the last insertion of a non-drug-eluting peripheral vessel stent treatment?"
            ),
            ["Procedure"],
        )

    def test_first_last_sort_is_emitted_only_for_registered_date_parameters(self):
        condition = {
            "question_id": "condition-first",
            "question": "what was the first diagnosis?",
            "patient_fhir_id": "synthetic-patient",
            "assumption": "",
            "split": "valid",
        }
        observation = {
            **condition,
            "question_id": "observation-first",
            "question": "what was the first heart rate value?",
        }

        condition_plan = a6.build_search_plan(
            condition, a6.qo_infer_intent(condition)
        )
        observation_plan = a6.build_search_plan(
            observation, a6.qo_infer_intent(observation)
        )

        self.assertEqual(len(condition_plan), 1)
        self.assertNotIn("_sort=", condition_plan[0]["path"])
        self.assertEqual(len(observation_plan), 2)
        self.assertTrue(all("_sort=" in item["path"] for item in observation_plan))

    def test_question_only_fallback_is_bounded_default(self):
        self.assertEqual(a6.qo_infer_resource_types("zzz qqq"), list(a6.QO_FALLBACK_TYPES))

    def test_packet_record_question_only_kind_and_no_gold(self):
        record = a6.build_packet_record(self.ROW, plan_only=True, resources_by_query={}, planner="question-only")
        dumped = str(record)
        self.assertEqual(record["packet"]["kind"], "a6a_question_only_packet")
        self.assertNotIn("secret", dumped)
        self.assertNotIn("chartevents", dumped)


class BoundsTests(unittest.TestCase):
    @staticmethod
    def _obs(i, day):
        return {
            "resourceType": "Observation",
            "id": f"o{i}",
            "effectiveDateTime": f"2100-01-{day:02d}T00:00:00Z",
            "text": {"div": "x" * 50},
            "meta": {"versionId": "1"},
            "valueQuantity": {"value": i},
        }

    def test_bounds_enforce_count_and_projection(self):
        resources = [self._obs(i, (i % 27) + 1) for i in range(300)]
        kept, stats = a6.bound_resources(
            resources, temporal_policy="recent", max_total_resources=40, max_packet_chars=1_000_000
        )
        self.assertEqual(len(kept), 40)
        self.assertEqual(stats["kept_count"], 40)
        self.assertTrue(all("text" not in r and "meta" not in r for r in kept))
        dates = [r["effectiveDateTime"] for r in kept]
        self.assertEqual(sorted(dates, reverse=True)[0], max(d["effectiveDateTime"] for d in map(a6.project_resource, resources)))

    def test_first_last_keeps_both_extremes(self):
        resources = [self._obs(i, i + 1) for i in range(20)]
        kept, _ = a6.bound_resources(
            resources, temporal_policy="first_last", max_total_resources=4, max_packet_chars=1_000_000
        )
        dates = {r["effectiveDateTime"] for r in kept}
        self.assertIn("2100-01-01T00:00:00Z", dates)
        self.assertIn("2100-01-20T00:00:00Z", dates)

    def test_char_budget_enforced(self):
        resources = [self._obs(i, (i % 27) + 1) for i in range(100)]
        kept, stats = a6.bound_resources(
            resources, temporal_policy="recent", max_total_resources=100, max_packet_chars=2_000
        )
        self.assertLess(stats["char_count"], 2_100)
        self.assertLess(len(kept), 100)

    def test_patient_always_survives(self):
        resources = [{"resourceType": "Patient", "id": "p1", "gender": "male"}] + [self._obs(i, 1) for i in range(50)]
        kept, _ = a6.bound_resources(
            resources, temporal_policy="recent", max_total_resources=5, max_packet_chars=1_000_000
        )
        self.assertTrue(any(r.get("resourceType") == "Patient" for r in kept))


class RelaxationTests(unittest.TestCase):
    def test_relax_drops_code_text_first(self):
        path = "Observation?patient=p1&_count=100&_sort=-date&date=ge2100-01-01&code:text=minimum%20respiratory%20rate"
        relaxed = a6.relax_query(path)
        self.assertNotIn("code:text", relaxed)
        self.assertIn("date=ge2100-01-01", relaxed)

    def test_relax_drops_dates_second_then_stops(self):
        path = "Observation?patient=p1&_count=100&_sort=-date&date=ge2100-01-01&date=le2100-12-31"
        relaxed = a6.relax_query(path)
        self.assertNotIn("date=ge", relaxed)
        self.assertNotIn("date=le", relaxed)
        self.assertIsNone(a6.relax_query(relaxed))

    def test_no_encounter_class_filter(self):
        row = {
            "question_id": "q20",
            "split": "valid",
            "question": "how many days was patient 1 in the icu during the last stay?",
            "assumption": "",
            "patient_fhir_id": "p20",
        }
        intent = a6.qo_infer_intent(row)
        plan = a6.build_search_plan(row, intent, count=50)
        for item in plan:
            self.assertNotIn("class=", item["path"])


class BluntProjectionTests(unittest.TestCase):
    ROW = {
        "question_id": "q30",
        "split": "valid",
        "question": "what was the last heart rate value of patient 1 since 12/2100?",
        "assumption": "assume that the current time is 2100-12-31 23:59:00",
        "patient_fhir_id": "p30",
        "main_table_name": "chartevents",
        "true_answer": "[[88]]",
    }

    def test_blunt_intent_is_query_blind(self):
        intent = a6.blunt_infer_intent(self.ROW)
        self.assertEqual(intent["search_terms"], [])
        self.assertEqual(intent["date_windows"], [])
        self.assertEqual(intent["resource_types"], list(a6.BLUNT_RESOURCE_TYPES))
        plan = a6.build_search_plan(self.ROW, intent, count=100)
        for item in plan:
            self.assertNotIn("code:text", item["path"])
            self.assertNotIn("date=ge", item["path"])

    def test_blunt_bound_caps_per_type_and_projects(self):
        resources = [
            {"resourceType": "Observation", "id": f"o{i}", "effectiveDateTime": f"2100-02-{(i % 27) + 1:02d}", "meta": {"v": 1}}
            for i in range(80)
        ] + [{"resourceType": "Encounter", "id": f"e{i}", "period": {"start": f"2100-01-{(i % 27) + 1:02d}"}} for i in range(10)]
        kept, stats = a6.blunt_bound(resources, per_type_cap=50)
        obs = [r for r in kept if r["resourceType"] == "Observation"]
        enc = [r for r in kept if r["resourceType"] == "Encounter"]
        self.assertEqual(len(obs), 50)
        self.assertEqual(len(enc), 10)
        self.assertTrue(all("meta" not in r for r in kept))
        self.assertEqual(stats["per_type_cap"], 50)

    def test_blunt_packet_kind(self):
        record = a6.build_packet_record(self.ROW, plan_only=True, resources_by_query={}, planner="blunt-projection")
        self.assertEqual(record["packet"]["kind"], "a0prime_blunt_packet")


if __name__ == "__main__":
    unittest.main()
