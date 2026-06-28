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


if __name__ == "__main__":
    unittest.main()
