import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_qt4_experiment as qt4


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _full_gate_fixture(root: Path):
    micro_ids = [f"micro-{index:03d}" for index in range(qt4.EXPECTED_MICRO)]
    non_micro_ids = [
        f"control-{index:03d}" for index in range(qt4.EXPECTED_NON_MICRO)
    ]
    all_ids = micro_ids + non_micro_ids
    input_path = root / "full409.csv"
    input_path.write_text(
        "question_id,question,main_table_name\n"
        + "".join(
            f"{qid},Question {qid},{'microbiologyevents' if qid in micro_ids else 'chartevents'}\n"
            for qid in all_ids
        ),
        encoding="utf-8",
    )
    arms = []
    for name in ("a6a", "qt4v", "qt4t"):
        packet_path = root / f"{name}.jsonl"
        packet_path.write_text(
            "".join(
                json.dumps({"question_id": qid, "packet": {"resources": []}})
                + "\n"
                for qid in all_ids
            ),
            encoding="utf-8",
        )
        arms.append(qt4.Arm(name, packet_path, root / f"run-{name}"))
    gate_inputs = {
        arm.name: {"path": str(arm.packet_path), "sha256": _sha(arm.packet_path)}
        for arm in arms
    }
    gate_inputs["question_spec"] = {
        "path": str(input_path),
        "sha256": _sha(input_path),
    }
    gates = [
        {"name": name, "passed": True, "observed": True, "expected": True}
        for name in sorted(qt4.REQUIRED_GATE_NAMES)
    ]
    gate = {
        "schema_version": qt4.GATE_SCHEMA_VERSION,
        "passed": True,
        "failed_gates": [],
        "gates": gates,
        "scheduled_question_count": qt4.EXPECTED_TOTAL,
        "scheduled_question_ids": all_ids,
        "inputs": gate_inputs,
        "dispatch": {
            "microbiology_questions": qt4.EXPECTED_MICRO,
            "non_microbiology_questions": qt4.EXPECTED_NON_MICRO,
            "microbiology_question_ids": micro_ids,
        },
        "equivalence": {
            "non_micro_packet": {
                "matched": qt4.EXPECTED_NON_MICRO,
                "total": qt4.EXPECTED_NON_MICRO,
            },
            "non_micro_prompt": {
                "matched": qt4.EXPECTED_NON_MICRO,
                "total": qt4.EXPECTED_NON_MICRO,
            },
        },
        "gate_expectations": {
            "expected_total": qt4.EXPECTED_TOTAL,
            "expected_micro": qt4.EXPECTED_MICRO,
            "expected_non_micro": qt4.EXPECTED_NON_MICRO,
        },
    }
    gate_path = root / "gate.json"
    gate_path.write_text(json.dumps(gate), encoding="utf-8")
    return input_path, micro_ids, arms, gate_path, gate


class Qt4ExperimentRunnerTests(unittest.TestCase):
    def test_frozen_micro42_spec_matches_dataset_rule_and_hash_order(self):
        repo = Path(__file__).resolve().parents[1]
        spec_path = repo / "docs" / "prereg" / "qt4_micro42_spec.json"
        input_path = repo / "final_dataset" / "full_test409.csv"

        spec_ids = qt4.validate_registered_question_spec(spec_path, input_path)
        with input_path.open(newline="", encoding="utf-8") as handle:
            selected = [
                row["question_id"]
                for row in csv.DictReader(handle)
                if row.get("main_table_name") == "microbiologyevents"
            ]
        expected = sorted(
            selected,
            key=lambda qid: hashlib.sha256(
                f"{qt4.REGISTERED_ORDER_SALT}{qid}".encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(spec_ids, expected)

    def test_load_spec_requires_unique_ids_and_declared_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.json"
            path.write_text(
                json.dumps(
                    {
                        "expected_question_count": 3,
                        "question_ids": ["q1", "q2", "q3"],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(qt4.load_question_spec(path), ["q1", "q2", "q3"])
            path.write_text(
                json.dumps(
                    {
                        "expected_question_count": 3,
                        "question_ids": ["q1", "q1", "q3"],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unique"):
                qt4.load_question_spec(path)

    def test_registered_model_and_effort_are_hard_pinned(self):
        qt4.validate_registered_execution(
            model=qt4.REGISTERED_MODEL,
            reasoning_effort=qt4.REGISTERED_REASONING_EFFORT,
        )
        for model, effort in (("other", "high"), (qt4.REGISTERED_MODEL, "low")):
            with self.subTest(model=model, effort=effort):
                with self.assertRaisesRegex(ValueError, "pinned"):
                    qt4.validate_registered_execution(
                        model=model, reasoning_effort=effort
                    )

    def test_schedule_rotates_arm_order_per_question(self):
        arms = [
            qt4.Arm("a6a", Path("a.jsonl"), Path("run-a")),
            qt4.Arm("qt4v", Path("v.jsonl"), Path("run-v")),
            qt4.Arm("qt4t", Path("t.jsonl"), Path("run-t")),
        ]
        schedule = qt4.interleaved_schedule(["q1", "q2", "q3"], arms)
        self.assertEqual(
            [(qid, arm.name) for qid, arm in schedule],
            [
                ("q1", "a6a"),
                ("q1", "qt4v"),
                ("q1", "qt4t"),
                ("q2", "qt4v"),
                ("q2", "qt4t"),
                ("q2", "a6a"),
                ("q3", "qt4t"),
                ("q3", "a6a"),
                ("q3", "qt4v"),
            ],
        )

    def test_preflight_requires_full_frozen_gate_and_exact_input_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path, micro_ids, arms, gate_path, gate = _full_gate_fixture(root)

            with mock.patch(
                "run_qt4_experiment.compare_packet_files", return_value=gate
            ):
                result = qt4.validate_preflight(
                    question_ids=micro_ids,
                    arms=arms,
                    gate_report_path=gate_path,
                    input_path=input_path,
                )
            self.assertEqual(result["gate_question_count"], qt4.EXPECTED_TOTAL)

            gate["gates"] = gate["gates"][1:]
            gate_path.write_text(json.dumps(gate), encoding="utf-8")
            with mock.patch(
                "run_qt4_experiment.compare_packet_files", return_value=gate
            ):
                with self.assertRaisesRegex(ValueError, "required gates"):
                    qt4.validate_preflight(
                        question_ids=micro_ids,
                        arms=arms,
                        gate_report_path=gate_path,
                        input_path=input_path,
                    )

    def test_preflight_rejects_micro_only_or_changed_dataset_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path, micro_ids, arms, gate_path, gate = _full_gate_fixture(root)
            gate["scheduled_question_count"] = qt4.EXPECTED_MICRO
            gate["scheduled_question_ids"] = micro_ids
            gate_path.write_text(json.dumps(gate), encoding="utf-8")
            with mock.patch(
                "run_qt4_experiment.compare_packet_files", return_value=gate
            ):
                with self.assertRaisesRegex(ValueError, "409"):
                    qt4.validate_preflight(
                        question_ids=micro_ids,
                        arms=arms,
                        gate_report_path=gate_path,
                        input_path=input_path,
                    )

            _, _, arms, gate_path, gate = _full_gate_fixture(root)
            changed = root / "changed.csv"
            changed.write_bytes(input_path.read_bytes() + b"\n")
            with mock.patch(
                "run_qt4_experiment.compare_packet_files", return_value=gate
            ):
                with self.assertRaisesRegex(ValueError, "question_spec hash"):
                    qt4.validate_preflight(
                        question_ids=micro_ids,
                        arms=arms,
                        gate_report_path=gate_path,
                        input_path=changed,
                    )

    def test_preflight_rejects_a_stored_gate_that_differs_from_recomputation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path, micro_ids, arms, gate_path, gate = _full_gate_fixture(root)
            recomputed = {**gate, "passed": False}
            with mock.patch(
                "run_qt4_experiment.compare_packet_files", return_value=recomputed
            ):
                with self.assertRaisesRegex(ValueError, "fresh deterministic"):
                    qt4.validate_preflight(
                        question_ids=micro_ids,
                        arms=arms,
                        gate_report_path=gate_path,
                        input_path=input_path,
                    )

    def test_output_directories_cannot_alias_or_nest(self):
        arms = [
            qt4.Arm("a6a", Path("a"), Path("runs/shared")),
            qt4.Arm("qt4v", Path("v"), Path("runs/shared")),
        ]
        with self.assertRaisesRegex(ValueError, "distinct"):
            qt4.validate_output_directories(arms)
        arms[1] = qt4.Arm("qt4v", Path("v"), Path("runs/shared/child"))
        with self.assertRaisesRegex(ValueError, "non-nested"):
            qt4.validate_output_directories(arms)

    def test_harness_command_is_single_question_and_runtime_pinned(self):
        arm = qt4.Arm("qt4v", Path("packets-v.jsonl"), Path("runs/qt4v"))
        command = qt4.build_harness_command(
            arm=arm,
            question_id="q7",
            input_path=Path("input.csv"),
            schema_path=Path("schema.json"),
            timeout=420,
            model=qt4.REGISTERED_MODEL,
            reasoning_effort=qt4.REGISTERED_REASONING_EFFORT,
            codex_bin="/opt/codex",
        )
        joined = " ".join(command)
        self.assertIn("--live", command)
        self.assertIn("--skip-existing", command)
        self.assertIn("--question-id q7", joined)
        self.assertIn(f"--model {qt4.REGISTERED_MODEL}", joined)
        self.assertIn("--reasoning-effort high", joined)
        self.assertIn("--codex-bin /opt/codex", joined)
        self.assertNotIn("--allow-full-run", command)

    def test_contamination_is_a_failure_not_terminal_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet_path = root / "packets.jsonl"
            packet_path.write_text("{}\n", encoding="utf-8")
            arm = qt4.Arm("qt4t", packet_path, root / "run")
            qdir = arm.out_dir / "questions" / "q1"
            qdir.mkdir(parents=True)
            (qdir / "contamination.json").write_text(
                json.dumps({"contaminated": True}), encoding="utf-8"
            )
            self.assertFalse(
                qt4.is_terminal_attempt(
                    arm, "q1", controller_manifest_sha256="manifest-sha"
                )
            )
            self.assertTrue(qt4._attempt_failure_exists(arm, "q1"))

    def test_completion_receipt_binds_clean_answer_and_file_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet_path = root / "packets.jsonl"
            packet_path.write_text("{}\n", encoding="utf-8")
            arm = qt4.Arm("a6a", packet_path, root / "run")
            qdir = arm.out_dir / "questions" / "q1"
            qdir.mkdir(parents=True)
            (qdir / "answer.json").write_text(
                json.dumps(
                    {
                        "answer": "42",
                        "source_resource_ids": [],
                        "evidence_summary": "evidence",
                        "insufficiency_reason": None,
                    }
                ),
                encoding="utf-8",
            )
            (qdir / "events.jsonl").write_text(
                json.dumps({"type": "turn.completed"}) + "\n", encoding="utf-8"
            )
            (qdir / "prompt.txt").write_text("prompt", encoding="utf-8")
            receipt = qt4._write_attempt_receipt(
                arm=arm,
                question_id="q1",
                controller_manifest_sha256="manifest-sha",
                returncode=0,
            )
            self.assertEqual(receipt["status"], "answered")
            self.assertTrue(
                qt4.is_terminal_attempt(
                    arm, "q1", controller_manifest_sha256="manifest-sha"
                )
            )
            (qdir / "prompt.txt").write_text("changed", encoding="utf-8")
            self.assertFalse(
                qt4.is_terminal_attempt(
                    arm, "q1", controller_manifest_sha256="manifest-sha"
                )
            )


if __name__ == "__main__":
    unittest.main()
