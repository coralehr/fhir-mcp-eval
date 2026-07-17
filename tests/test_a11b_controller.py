from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import a11b_controller


class A11bControllerTests(unittest.TestCase):
    def test_audit_manifest_verification_never_opens_gold_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {
                "schema_version": "a11b-audit-corpus-manifest-v1",
                "model_calls": 0,
                "split_counts": {"development": 64, "efficacy": 384},
                "artifacts": {
                    "efficacy/gold.jsonl": {"sha256": "f" * 64, "bytes": 1}
                },
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


if __name__ == "__main__":
    unittest.main()
