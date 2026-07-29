import unittest

import c3g_explore


def recursive_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(recursive_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(recursive_keys(item) for item in value))
    return set()


def record(question: str = "How many times during the current hospital visit?") -> dict:
    return {
        "question_id": "q1",
        "question": question,
        "patient_fhir_id": "patient-1",
        "packet": {
            "resources": [
                {
                    "resourceType": "Patient",
                    "id": "patient-1",
                }
            ]
        },
    }


def hospital_encounter(
    encounter_id: str,
    *,
    status: str = "finished",
    patient_id: str = "patient-1",
) -> dict:
    return {
        "resourceType": "Encounter",
        "id": encounter_id,
        "status": status,
        "subject": {"reference": f"Patient/{patient_id}"},
        "identifier": [
            {
                "system": "http://mimic.mit.edu/fhir/mimic/identifier/encounter-hosp",
                "value": encounter_id,
            }
        ],
    }


class C3GExploreTests(unittest.TestCase):
    def test_empty_scope_operator_distinguishes_count_exists_and_scalar(self) -> None:
        self.assertEqual(
            c3g_explore.empty_scope_operator(
                "How many times was thoracentesis performed during the current visit?"
            ),
            "count",
        )
        self.assertEqual(
            c3g_explore.empty_scope_operator(
                "Has patient received amlodipine during the current hospital visit?"
            ),
            "exists",
        )
        self.assertEqual(
            c3g_explore.empty_scope_operator(
                "When was the last laboratory test during the current visit?"
            ),
            "scalar",
        )

    def test_finished_store_encounters_compile_zero_count_receipt(self) -> None:
        packet, receipt = c3g_explore.store_complete_encounter_packet(
            record(),
            [hospital_encounter("encounter-1")],
        )

        self.assertEqual(receipt["state"], "no_active_hospital_encounter_in_store")
        self.assertTrue(receipt["terminal_page_reached"])
        self.assertEqual(receipt["hospital_status_counts"], {"finished": 1})
        self.assertEqual(
            receipt["empty_scope_semantics"],
            {
                "operator": "count",
                "empty_value": 0,
                "meaning": "zero matching events",
                "sufficient_for_answer": True,
                "reason": (
                    "The question requires an event inside the current hospital Encounter. "
                    "The complete current-Encounter set has cardinality zero, so the dependent "
                    "event set is empty without fetching event resources."
                ),
            },
        )
        self.assertEqual(packet["packet"]["resource_count"], 2)
        self.assertNotIn("answer", recursive_keys(packet))

    def test_active_encounter_does_not_compile_empty_scope_result(self) -> None:
        _, receipt = c3g_explore.store_complete_encounter_packet(
            record("Has patient received medication during the current visit?"),
            [hospital_encounter("encounter-1", status="in-progress")],
        )

        self.assertEqual(receipt["state"], "selected_active_hospital_encounter")
        self.assertEqual(receipt["selected_encounter"], "Encounter/encounter-1")
        self.assertNotIn("empty_scope_semantics", receipt)

    def test_patient_inconsistent_search_result_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "patient-inconsistent"):
            c3g_explore.store_complete_encounter_packet(
                record(),
                [hospital_encounter("encounter-1", patient_id="patient-2")],
            )


if __name__ == "__main__":
    unittest.main()
