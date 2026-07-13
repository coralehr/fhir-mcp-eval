import unittest

import replace_packet_rows as replace


class ReplacePacketRowsTests(unittest.TestCase):
    def test_replaces_exact_frozen_ids_and_preserves_base_order(self):
        base = [
            {"question_id": "q1", "packet": {"arm": "old"}},
            {
                "question_id": "q2",
                "question": "Frozen question",
                "question_with_context": "Frozen context",
                "patient_fhir_id": "Patient/frozen",
                "assumption": "Frozen assumption",
                "intent": {"planner": "frozen"},
                "packet": {"arm": "old"},
            },
            {"question_id": "q3", "packet": {"arm": "old"}},
        ]
        updates = [
            {
                "question_id": "q2",
                "question": "Tampered question",
                "question_with_context": "Tampered context",
                "patient_fhir_id": "Patient/tampered",
                "assumption": "Tampered assumption",
                "intent": {"planner": "tampered"},
                "packet": {"arm": "new"},
                "new_top_level_field": "must not be copied",
            }
        ]

        result = replace.replace_records(
            base,
            updates,
            ["q2"],
            expected_total=3,
            expected_updates=1,
        )

        self.assertEqual([record["question_id"] for record in result], ["q1", "q2", "q3"])
        self.assertEqual(result[1]["packet"]["arm"], "new")
        self.assertEqual(
            {key: value for key, value in result[1].items() if key != "packet"},
            {key: value for key, value in base[1].items() if key != "packet"},
        )
        self.assertNotIn("new_top_level_field", result[1])
        self.assertIs(result[0], base[0])

    def test_rejects_partial_or_extra_update_set(self):
        base = [{"question_id": "q1"}, {"question_id": "q2"}]
        invalid_updates = (
            [{"question_id": "q1"}],
            [{"question_id": "q1"}, {"question_id": "q-extra"}],
        )
        for updates in invalid_updates:
            with self.subTest(updates=updates):
                with self.assertRaisesRegex(ValueError, "exactly match"):
                    replace.replace_records(
                        base,
                        updates,
                        ["q1", "q2"],
                        expected_total=2,
                        expected_updates=2,
                    )

    def test_rejects_update_without_packet_object(self):
        with self.assertRaisesRegex(ValueError, "has no packet object"):
            replace.replace_records(
                [{"question_id": "q1", "packet": {}}],
                [{"question_id": "q1", "packet": None}],
                ["q1"],
                expected_total=1,
                expected_updates=1,
            )


if __name__ == "__main__":
    unittest.main()
