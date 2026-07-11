import unittest

import a6_packet_builder as a6


class A6PacketBuilderTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
