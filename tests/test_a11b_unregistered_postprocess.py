from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import a11b_unregistered_postprocess as postprocess
import a11b_unregistered_preview as preview
import codex_harness
import run_a11b_panel


class A11bUnregisteredPostprocessTests(unittest.TestCase):
    def test_selected_answer_follows_validated_normalization_marker(self):
        registered_schema = Path("schemas/a11b_answer.schema.json").read_bytes()
        transport_schema = preview._transport_schema(registered_schema)
        answer = {
            "answer": "Substantive answer",
            "source_resource_ids": ["Observation/example"],
            "evidence_summary": "Visible evidence.",
            "insufficiency_reason": "A non-answer detail was unavailable.",
        }
        with tempfile.TemporaryDirectory() as directory:
            slot_dir = Path(directory) / "0000"
            attempt_dir = slot_dir / "attempt-1"
            attempt_dir.mkdir(parents=True)
            (attempt_dir / "answer.json").write_text(
                json.dumps(answer), encoding="utf-8"
            )
            (attempt_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
            (attempt_dir / "stderr.log").write_bytes(b"")
            (attempt_dir / "registered-schema.json").write_bytes(registered_schema)
            (attempt_dir / "transport-schema.json").write_bytes(transport_schema)
            receipt = preview._receipt_for_attempt(
                index=0,
                attempt_number=1,
                attempt_dir=attempt_dir,
                outcome="provider_failure",
                error=preview.NORMALIZABLE_SCHEMA_ERROR,
            )
            preview._atomic_write(attempt_dir / "receipt.json", receipt)
            with mock.patch.object(
                codex_harness, "enforce_packet_event_integrity"
            ):
                preview._recover_normalized_attempt(
                    slot_dir=slot_dir,
                    attempt_dir=attempt_dir,
                    receipt=receipt,
                    index=0,
                    attempt_number=1,
                    prompt_sha256="a" * 64,
                    registered_schema=registered_schema,
                    transport_schema=transport_schema,
                )

            selected, selected_receipt, _attempt_receipt = (
                postprocess._selected_answer(
                    slot_dir=slot_dir,
                    index=0,
                    host={
                        "arm": "t0",
                        "question_id": "q-000",
                        "prompt_sha256": "a" * 64,
                    },
                )
            )

            self.assertIsNone(selected["insufficiency_reason"])
            self.assertEqual(
                selected_receipt["acceptance_mode"],
                "deterministic_normalization",
            )
            self.assertEqual(
                selected_receipt["answer_artifact"], "normalized-answer.json"
            )

    def test_panel_prompt_excludes_host_arm_and_question_identity(self):
        codex = run_a11b_panel.CodexIdentity(
            path=Path("/tmp/pinned-codex"),
            version="codex-cli 0.144.1",
            sha256="b" * 64,
        )
        config = run_a11b_panel.build_judge_config(
            controller_manifest_sha256="a" * 64, codex=codex
        )
        queue = [
            {
                "arm": "e1",
                "question_id": "secret-host-id",
                "question": "What categorical finding is present?",
                "acceptable_any": ["X", "Finding X"],
                "answer": "X",
                "insufficiency_reason": None,
            }
        ]
        blinded = run_a11b_panel.prepare_blinded_items(queue, config)
        prompt = run_a11b_panel.batch_prompt(blinded)

        self.assertNotIn('"arm"', prompt)
        self.assertNotIn("secret-host-id", prompt)
        self.assertNotIn('"e1"', prompt)
        self.assertIn(blinded[0]["opaque_id"], prompt)

    def test_status_tolerates_one_live_attempt_without_a_receipt(self):
        codex = run_a11b_panel.CodexIdentity(
            path=Path("/tmp/pinned-codex"),
            version="codex-cli 0.144.1",
            sha256="b" * 64,
        )
        config = run_a11b_panel.build_judge_config(
            controller_manifest_sha256="a" * 64, codex=codex
        )
        blinded = run_a11b_panel.prepare_blinded_items(
            [
                {
                    "arm": "t0",
                    "question_id": "q-000",
                    "question": "What categorical finding is present?",
                    "acceptable_any": ["X", "Finding X"],
                    "answer": "X",
                    "insufficiency_reason": None,
                }
            ],
            config,
        )
        manifest = run_a11b_panel.build_manifest_identity(
            controller_manifest_sha256="a" * 64,
            queue_sha256="c" * 64,
            judge_config=config,
            blinded_items=blinded,
        )
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory)
            attempt = run_a11b_panel._attempt_root(out_dir, 0, 0) / "attempt-001"
            attempt.mkdir(parents=True)

            votes, active = postprocess._panel_progress(
                out_dir=out_dir, manifest=manifest, blinded=blinded
            )

        self.assertEqual(active, 1)
        self.assertEqual(votes[blinded[0]["opaque_id"]], [])


if __name__ == "__main__":
    unittest.main()
