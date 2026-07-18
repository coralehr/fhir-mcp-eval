from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import a11_answer_harness
import a11b_controller


class A11bControllerTests(unittest.TestCase):
    def test_audit_manifest_verification_never_opens_gold_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {
                "schema_version": "a11b-audit-corpus-manifest-v1",
                "model_calls": 0,
                "split_counts": {"development": 64, "efficacy": 384},
                "artifacts": {"efficacy/gold.jsonl": {"sha256": "f" * 64, "bytes": 1}},
            }
            payload = (
                json.dumps(manifest, separators=(",", ":"), sort_keys=True) + "\n"
            ).encode()
            (root / "manifest.json").write_bytes(payload)
            (root / "manifest.sha256").write_text(
                hashlib.sha256(payload).hexdigest() + "\n"
            )

            observed, digest = a11b_controller._verify_audit_manifest_only(root)

            self.assertEqual(observed, manifest)
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
            self.assertFalse((root / "efficacy/gold.jsonl").exists())

    def test_rotating_schedule_is_balanced_and_exact(self) -> None:
        question_ids = [f"q-{index:03d}" for index in range(384)]

        schedule = a11b_controller.rotating_schedule(question_ids)

        self.assertEqual(len(schedule), 1152)
        self.assertEqual(
            schedule[:9],
            [
                ("q-000", "t0"),
                ("q-000", "t1"),
                ("q-000", "e1"),
                ("q-001", "t1"),
                ("q-001", "e1"),
                ("q-001", "t0"),
                ("q-002", "e1"),
                ("q-002", "t0"),
                ("q-002", "t1"),
            ],
        )
        for position in range(3):
            counts = {
                arm: sum(
                    1
                    for index in range(position, len(schedule), 3)
                    if schedule[index][1] == arm
                )
                for arm in a11b_controller.ARMS
            }
            self.assertEqual(set(counts.values()), {128})

    def test_schedule_rejects_duplicate_or_wrong_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "384"):
            a11b_controller.rotating_schedule(["q"] * 384)
        with self.assertRaisesRegex(ValueError, "384"):
            a11b_controller.rotating_schedule([f"q-{index}" for index in range(383)])

    def test_successor_schedule_is_exact_balanced_and_development_only(self) -> None:
        question_ids = [f"dev-{index:03d}" for index in range(64)]

        schedule = a11b_controller.successor_rotating_schedule(question_ids)

        self.assertEqual(len(schedule), 192)
        self.assertEqual(
            schedule[:6],
            [
                ("dev-000", "t0"),
                ("dev-000", "t1"),
                ("dev-000", "e1"),
                ("dev-001", "t1"),
                ("dev-001", "e1"),
                ("dev-001", "t0"),
            ],
        )
        for position in range(3):
            counts = {
                arm: sum(
                    schedule[index][1] == arm
                    for index in range(position, len(schedule), 3)
                )
                for arm in a11b_controller.ARMS
            }
            self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
        for invalid in (
            ["dev"] * 64,
            [f"dev-{index}" for index in range(63)],
            [f"reserved-efficacy-{index}" for index in range(64)],
        ):
            with self.subTest(size=len(invalid)), self.assertRaises(ValueError):
                a11b_controller.successor_rotating_schedule(invalid)

    def test_successor_prompt_inventory_opens_development_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            development = root / "development"
            development.mkdir()
            rows = [
                {
                    "question_id": f"dev-{index:03d}",
                    "question": f"Question {index}?",
                    "assumption": "Use only the supplied packet.",
                }
                for index in range(64)
            ]
            csv_lines = ["question_id,question,assumption"] + [
                f"{row['question_id']},{row['question']},{row['assumption']}"
                for row in rows
            ]
            (development / "answer_input.csv").write_text(
                "\n".join(csv_lines) + "\n", encoding="utf-8"
            )
            for arm in a11b_controller.ARMS:
                records = [
                    a11_answer_harness.make_successor_prompt_record(
                        row, json.dumps({"visible": index}, separators=(",", ":"))
                    )
                    for index, row in enumerate(rows)
                ]
                (development / f"{arm}_packets.jsonl").write_text(
                    "".join(
                        json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
                        for record in records
                    ),
                    encoding="utf-8",
                )

            question_ids, prompts = a11b_controller._successor_prompt_inventory(root)

            self.assertEqual(question_ids, [row["question_id"] for row in rows])
            self.assertEqual(len(prompts), 192)
            self.assertFalse((root / "efficacy").exists())

    def test_successor_audit_verification_does_not_open_gold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {
                "schema_version": "a11b-successor-development-audit-manifest-v1",
                "model_calls": 0,
                "split_counts": {"development": 64},
                "reserved_efficacy_patient_count": 384,
                "efficacy_materialized": False,
                "artifacts": {
                    "development/gold.jsonl": {"sha256": "f" * 64, "bytes": 1}
                },
            }
            payload = (
                json.dumps(manifest, separators=(",", ":"), sort_keys=True) + "\n"
            ).encode()
            (root / "manifest.json").write_bytes(payload)
            (root / "manifest.sha256").write_text(
                hashlib.sha256(payload).hexdigest() + "\n"
            )

            observed, digest = a11b_controller._verify_successor_audit_manifest_only(
                root
            )

            self.assertEqual(observed, manifest)
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
            self.assertFalse((root / "development/gold.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
