import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import panel_grade


def queue_item(
    arm: str,
    question_id: str,
    *,
    question: str = "What is the result?",
    gold: str = "positive",
    answer: str = "Positive.",
) -> dict:
    return {
        "arm": arm,
        "question_id": question_id,
        "question": question,
        "gold": gold,
        "answer": answer,
        "insufficiency_reason": None,
    }


def judge_config(**overrides) -> dict:
    values = {
        "model": "gpt-test",
        "effort": "high",
        "batch_size": 2,
        "votes": 3,
        "timeout": 600,
        "codex_bin": "/opt/codex",
        "codex_version": "codex 1.2.3",
    }
    values.update(overrides)
    return panel_grade.build_judge_config(**values)


class PanelBlindnessTests(unittest.TestCase):
    def test_prompt_uses_only_opaque_ids_and_never_exposes_arm_or_question_id(self):
        queue = [
            queue_item("experimental-arm-secret", "raw-question-id-001"),
            queue_item("control-arm-secret", "raw-question-id-002"),
        ]
        blinded = panel_grade.prepare_blinded_items(queue, judge_config())

        prompt = panel_grade.batch_prompt(blinded)

        self.assertNotIn("experimental-arm-secret", prompt)
        self.assertNotIn("control-arm-secret", prompt)
        self.assertNotIn("raw-question-id-001", prompt)
        self.assertNotIn("raw-question-id-002", prompt)
        self.assertNotIn("experimental-arm-secret|raw-question-id-001", prompt)
        for item in blinded:
            self.assertRegex(item["opaque_id"], r"^panel_[0-9a-f]{32}$")
            self.assertIn(item["opaque_id"], prompt)

    def test_interleaving_is_deterministic_input_order_independent_and_arm_alternating(self):
        queue = [
            queue_item("a6a", "q1"),
            queue_item("a6a", "q2"),
            queue_item("a0prime", "q1"),
            queue_item("a0prime", "q2"),
        ]
        config = judge_config()
        forward = panel_grade.prepare_blinded_items(queue, config)
        reversed_items = panel_grade.prepare_blinded_items(list(reversed(queue)), config)

        first = panel_grade.deterministic_interleave(forward, vote_round=0)
        second = panel_grade.deterministic_interleave(reversed_items, vote_round=0)

        self.assertEqual(
            [item["opaque_id"] for item in first],
            [item["opaque_id"] for item in second],
        )
        self.assertEqual(len(first), 4)
        self.assertTrue(
            all(
                left["host"]["arm"] != right["host"]["arm"]
                for left, right in zip(first, first[1:])
            )
        )

    def test_opaque_cache_key_binds_all_judged_content_and_judge_configuration(self):
        base_item = queue_item("a6a", "q1")
        base_config = judge_config()
        base_id = panel_grade.prepare_blinded_items([base_item], base_config)[0][
            "opaque_id"
        ]

        variants = [
            {**base_item, "answer": "negative"},
            {**base_item, "gold": "negative"},
            {**base_item, "question": "Was the result negative?"},
            {**base_item, "insufficiency_reason": "not enough evidence"},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                variant_id = panel_grade.prepare_blinded_items(
                    [variant], base_config
                )[0]["opaque_id"]
                self.assertNotEqual(base_id, variant_id)

        for changed_config in [
            judge_config(model="another-model"),
            judge_config(effort="medium"),
            judge_config(batch_size=1),
            judge_config(votes=5),
            judge_config(timeout=300),
            judge_config(codex_bin="/another/codex"),
            judge_config(codex_version="codex 9.9.9"),
        ]:
            with self.subTest(config=changed_config):
                variant_id = panel_grade.prepare_blinded_items(
                    [base_item], changed_config
                )[0]["opaque_id"]
                self.assertNotEqual(base_id, variant_id)

        with mock.patch.object(
            panel_grade,
            "JUDGE_PREAMBLE",
            panel_grade.JUDGE_PREAMBLE + "\nProtocol clarification.",
        ):
            prompt_changed_config = judge_config()
        prompt_changed_id = panel_grade.prepare_blinded_items(
            [base_item], prompt_changed_config
        )[0]["opaque_id"]
        self.assertNotEqual(base_id, prompt_changed_id)

        with mock.patch.object(
            panel_grade,
            "JUDGE_PROTOCOL_VERSION",
            "panel-judge-test-successor",
        ):
            protocol_changed_config = judge_config()
        protocol_changed_id = panel_grade.prepare_blinded_items(
            [base_item], protocol_changed_config
        )[0]["opaque_id"]
        self.assertNotEqual(base_id, protocol_changed_id)

    def test_cache_reuse_requires_exact_versioned_manifest_and_item_bindings(self):
        queue = [queue_item("a6a", "q1"), queue_item("a0prime", "q1")]
        config = judge_config()
        blinded = panel_grade.prepare_blinded_items(queue, config)
        manifest = panel_grade.build_cache_manifest(blinded, config)

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "votes.json"
            cache = panel_grade.load_or_initialize_cache(
                cache_path, manifest, blinded
            )
            first_id = blinded[0]["opaque_id"]
            cache["items"][first_id]["votes"].append(True)
            panel_grade.write_cache(cache_path, cache)

            resumed = panel_grade.load_or_initialize_cache(
                cache_path, manifest, blinded
            )
            self.assertEqual(resumed["items"][first_id]["votes"], [True])

            changed = panel_grade.prepare_blinded_items(
                [{**queue[0], "answer": "changed"}, queue[1]], config
            )
            changed_manifest = panel_grade.build_cache_manifest(changed, config)
            with self.assertRaisesRegex(ValueError, "manifest mismatch"):
                panel_grade.load_or_initialize_cache(
                    cache_path, changed_manifest, changed
                )

    def test_legacy_raw_key_cache_is_rejected_instead_of_silently_reused(self):
        item = queue_item("a6a", "q1")
        config = judge_config()
        blinded = panel_grade.prepare_blinded_items([item], config)
        manifest = panel_grade.build_cache_manifest(blinded, config)

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "votes.json"
            cache_path.write_text(
                json.dumps({"a6a|q1": [True, True, True]}), encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "legacy or unsupported"):
                panel_grade.load_or_initialize_cache(
                    cache_path, manifest, blinded
                )

    def test_majority_verdicts_restore_host_side_arm_question_mapping(self):
        queue = [queue_item("a6a", "q1"), queue_item("a0prime", "q1")]
        config = judge_config()
        blinded = panel_grade.prepare_blinded_items(queue, config)
        manifest = panel_grade.build_cache_manifest(blinded, config)
        cache = panel_grade.new_cache(manifest, blinded)
        by_arm = {item["host"]["arm"]: item for item in blinded}
        cache["items"][by_arm["a6a"]["opaque_id"]]["votes"] = [True, True, False]
        cache["items"][by_arm["a0prime"]["opaque_id"]]["votes"] = [False, False, True]

        verdicts = panel_grade.majority_verdicts(cache, required_votes=3)

        self.assertEqual(verdicts, {"a6a|q1": 1, "a0prime|q1": 0})
        self.assertTrue(all(re.fullmatch(r"[^|]+\|[^|]+", key) for key in verdicts))

    def test_run_vote_rejects_missing_extra_duplicate_and_non_boolean_verdicts(self):
        blinded = panel_grade.prepare_blinded_items(
            [queue_item("a6a", "q1")], judge_config()
        )
        opaque_id = blinded[0]["opaque_id"]

        invalid_verdicts = [
            [],
            [{"item_id": "a6a|q1", "correct": True}],
            [
                {"item_id": opaque_id, "correct": True},
                {"item_id": opaque_id, "correct": False},
            ],
            [{"item_id": opaque_id, "correct": "false"}],
        ]
        for verdicts in invalid_verdicts:
            with self.subTest(verdicts=verdicts):

                def fake_run(command, **_kwargs):
                    output_path = Path(
                        command[command.index("--output-last-message") + 1]
                    )
                    output_path.write_text(
                        json.dumps({"verdicts": verdicts}), encoding="utf-8"
                    )
                    return SimpleNamespace(returncode=0, stderr="")

                with mock.patch("panel_grade.subprocess.run", side_effect=fake_run):
                    with self.assertRaises(RuntimeError):
                        panel_grade.run_vote(
                            blinded,
                            codex_bin="codex",
                            timeout=30,
                            model="gpt-test",
                            effort="high",
                        )

    def test_run_vote_accepts_exact_opaque_coverage(self):
        blinded = panel_grade.prepare_blinded_items(
            [queue_item("a6a", "q1")], judge_config()
        )
        opaque_id = blinded[0]["opaque_id"]
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            output_path = Path(
                command[command.index("--output-last-message") + 1]
            )
            output_path.write_text(
                json.dumps(
                    {"verdicts": [{"item_id": opaque_id, "correct": True}]}
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stderr="")

        with mock.patch("panel_grade.subprocess.run", side_effect=fake_run):
            result = panel_grade.run_vote(
                blinded,
                codex_bin="codex",
                timeout=30,
                model="gpt-test",
                effort="high",
            )

        self.assertEqual(result, {opaque_id: True})
        self.assertIn("--ephemeral", commands[0])
        self.assertIn("--ignore-user-config", commands[0])
        self.assertIn("--ignore-rules", commands[0])


if __name__ == "__main__":
    unittest.main()
