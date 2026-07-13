import unittest

import replace_packet_rows as replace


class ReplacePacketRowsTests(unittest.TestCase):
    def test_replaces_exact_frozen_ids_and_preserves_base_order(self):
        base = [
            {"question_id": "q1", "packet": {"arm": "old"}},
            {"question_id": "q2", "packet": {"arm": "old"}},
            {"question_id": "q3", "packet": {"arm": "old"}},
        ]
        updates = [{"question_id": "q2", "packet": {"arm": "new"}}]

        result = replace.replace_records(
            base,
            updates,
            ["q2"],
            expected_total=3,
            expected_updates=1,
        )

        self.assertEqual([record["question_id"] for record in result], ["q1", "q2", "q3"])
        self.assertEqual(result[1]["packet"]["arm"], "new")
        self.assertIs(result[0], base[0])

    def test_rejects_partial_or_extra_update_set(self):
        base = [{"question_id": "q1"}, {"question_id": "q2"}]
        with self.assertRaisesRegex(ValueError, "exactly match"):
            replace.replace_records(
                base,
                [{"question_id": "q1"}],
                ["q1", "q2"],
                expected_total=2,
                expected_updates=2,
            )


if __name__ == "__main__":
    unittest.main()
