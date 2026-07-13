import contextlib
import csv
import fcntl
import hashlib
import json
import os
import subprocess
import sys
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
    def test_bootstrap_bundle_is_complete_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = Path(tmp) / "controller" / "manifest.json"
            runner = qt4._stage_bootstrap_bundle(controller)
            stage_dir = controller.parent / "bootstrap"

            self.assertEqual(
                runner.resolve(),
                (stage_dir / "run_qt4_experiment.py").resolve(),
            )
            manifest = json.loads(
                (stage_dir / "bootstrap-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(set(manifest["files"]), set(qt4._BOOTSTRAP_FILES))
            self.assertEqual(stage_dir.stat().st_mode & 0o222, 0)
            self.assertTrue(
                all(path.stat().st_mode & 0o222 == 0 for path in stage_dir.iterdir())
            )
            self.assertEqual(
                qt4._verify_bootstrap_bundle(stage_dir).resolve(),
                runner.resolve(),
            )

            runner.chmod(0o644)
            runner.write_text("tampered\n", encoding="utf-8")
            runner.chmod(0o444)
            with self.assertRaisesRegex(SystemExit, "bootstrap file changed"):
                qt4._verify_bootstrap_bundle(stage_dir)
            stage_dir.chmod(0o755)
            for path in stage_dir.iterdir():
                path.chmod(0o644)

    def test_loaded_sources_must_match_verified_bootstrap_hashes(self):
        source_paths = {
            "runner": Path(qt4.__file__).resolve(),
            "harness": Path(qt4.sys.modules["codex_harness"].__file__).resolve(),
            "gate_code": Path(qt4.sys.modules["qt4_packet_gate"].__file__).resolve(),
            "run_lock": Path(qt4.sys.modules["run_lock"].__file__).resolve(),
        }
        source_hashes = {name: _sha(path) for name, path in source_paths.items()}
        bootstrap_hashes = {
            filename: source_hashes[source_name]
            for source_name, filename in qt4._BOOTSTRAP_SOURCE_BINDINGS.items()
        }
        with mock.patch.object(
            qt4,
            "_VERIFIED_BOOTSTRAP_HASHES",
            bootstrap_hashes,
        ):
            qt4._validate_loaded_bootstrap_binding(source_hashes)
            changed = {**source_hashes, "harness": "0" * 64}
            with self.assertRaisesRegex(ValueError, "bootstrap manifest"):
                qt4._validate_loaded_bootstrap_binding(changed)

    def test_registered_harness_path_rejects_even_identical_override(self):
        expected = Path(qt4.__file__).resolve().with_name("codex_harness.py")
        qt4.validate_registered_harness_path(expected)
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "codex_harness.py"
            override.write_bytes(expected.read_bytes())
            with self.assertRaisesRegex(ValueError, "pinned to the staged path"):
                qt4.validate_registered_harness_path(override)

    def test_staged_child_adopts_preimport_lock_without_self_deadlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "controller.lock"
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.set_inheritable(fd, True)
            environment = {
                qt4._BOOTSTRAPPED_ENV: "1",
                qt4._PRELOCK_FD_ENV: str(fd),
                qt4._PRELOCK_PATH_ENV: str(lock_path.resolve()),
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                with qt4._acquire_live_instance_lock(lock_path):
                    self.assertFalse(os.get_inheritable(fd))
                    contender = os.open(lock_path, os.O_RDWR)
                    try:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(
                                contender,
                                fcntl.LOCK_EX | fcntl.LOCK_NB,
                            )
                    finally:
                        os.close(contender)

            contender = os.open(lock_path, os.O_RDWR)
            try:
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(contender, fcntl.LOCK_UN)
            finally:
                os.close(contender)

    def test_status_fails_closed_when_controller_bootstrap_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            controller = Path(tmp) / "controller" / "manifest.json"
            controller.parent.mkdir(parents=True)
            controller.write_text("{}\n", encoding="utf-8")
            argv = [
                "run_qt4_experiment.py",
                "--controller-manifest",
                str(controller),
                "--status",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                self.assertRaisesRegex(SystemExit, "without its immutable bootstrap"),
            ):
                qt4._exec_immutable_bootstrap(live=False)

    def test_live_cli_reexecs_under_lock_without_locking_itself_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = root / "controller" / "manifest.json"
            lock_path = root / "controller.lock"
            runner = Path(qt4.__file__).resolve()
            command = [
                sys.executable,
                str(runner),
                "--spec",
                str(root / "missing-spec.json"),
                "--gate-report",
                str(root / "missing-gate.json"),
                "--a6a-packets",
                str(root / "missing-a.jsonl"),
                "--qt4v-packets",
                str(root / "missing-v.jsonl"),
                "--qt4t-packets",
                str(root / "missing-t.jsonl"),
                "--a6a-out",
                str(root / "out-a"),
                "--qt4v-out",
                str(root / "out-v"),
                "--qt4t-out",
                str(root / "out-t"),
                "--controller-manifest",
                str(controller),
                "--lock",
                str(lock_path),
                "--live",
            ]

            result = subprocess.run(command, capture_output=True, text=True)

            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertNotIn("ALREADY_RUNNING", result.stdout)
            self.assertIn("FileNotFoundError", result.stderr)
            self.assertTrue((controller.parent / "bootstrap" / runner.name).is_file())
            contender = os.open(lock_path, os.O_RDWR)
            try:
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(contender, fcntl.LOCK_UN)
            finally:
                os.close(contender)

    def test_live_controller_takes_lock_before_source_validation(self):
        locked = False

        @contextlib.contextmanager
        def fake_lock():
            nonlocal locked
            locked = True
            try:
                yield
            finally:
                locked = False

        class ValidationReached(RuntimeError):
            pass

        def validate_inside_lock(**_kwargs):
            self.assertTrue(locked)
            raise ValidationReached

        argv = [
            "run_qt4_experiment.py",
            "--spec",
            "spec.json",
            "--gate-report",
            "gate.json",
            "--a6a-packets",
            "a.jsonl",
            "--qt4v-packets",
            "v.jsonl",
            "--qt4t-packets",
            "t.jsonl",
            "--live",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(qt4, "acquire_single_instance", return_value=fake_lock()),
            mock.patch.object(
                qt4, "validate_and_bind_sources", side_effect=validate_inside_lock
            ),
            self.assertRaises(ValidationReached),
        ):
            qt4.main()

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

    def test_source_binding_detects_post_validation_packet_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path, micro_ids, arms, gate_path, gate = _full_gate_fixture(root)
            ordered_ids = sorted(
                micro_ids,
                key=lambda qid: hashlib.sha256(
                    f"{qt4.REGISTERED_ORDER_SALT}{qid}".encode("utf-8")
                ).hexdigest(),
            )
            spec_path = root / "spec.json"
            spec_path.write_text(
                json.dumps(
                    {
                        "kind": qt4.REGISTERED_SPEC_KIND,
                        "version": qt4.REGISTERED_SPEC_VERSION,
                        "order_method": qt4.REGISTERED_ORDER_METHOD,
                        "expected_question_count": qt4.EXPECTED_MICRO,
                        "question_ids": ordered_ids,
                    }
                ),
                encoding="utf-8",
            )

            def mutate_packet():
                arms[0].packet_path.write_bytes(
                    arms[0].packet_path.read_bytes() + b"\n"
                )

            with mock.patch(
                "run_qt4_experiment.compare_packet_files", return_value=gate
            ):
                with self.assertRaisesRegex(ValueError, "packet_a6a"):
                    qt4.validate_and_bind_sources(
                        spec_path=spec_path,
                        gate_report_path=gate_path,
                        input_path=input_path,
                        arms=arms,
                        post_validation_hook=mutate_packet,
                    )

    def test_snapshot_copy_rejects_mutated_source_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jsonl"
            destination = root / "snapshot.jsonl"
            source.write_text("original\n", encoding="utf-8")
            entry = {
                "source_path": str(source),
                "snapshot_path": str(destination),
                "sha256": _sha(source),
            }
            source.write_text("mutated\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "source changed"):
                qt4._copy_or_verify_snapshot(entry)

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
            self.assertEqual(
                qt4._blocking_artifact_reason(
                    arm,
                    "q1",
                    controller_manifest_sha256="manifest-sha",
                ),
                "contamination_marker",
            )
            receipt = qt4._write_attempt_receipt(
                arm=arm,
                question_id="q1",
                controller_manifest_sha256="manifest-sha",
                returncode=1,
                attempt_number=1,
            )
            qt4._archive_failed_attempt(
                arm=arm,
                question_id="q1",
                receipt={**receipt, "status": "contaminated"},
            )
            (qdir / "contamination.json").unlink()
            self.assertEqual(
                qt4._blocking_artifact_reason(
                    arm,
                    "q1",
                    controller_manifest_sha256="manifest-sha",
                ),
                "archived_contaminated",
            )

    def test_orphan_answer_and_cross_controller_receipt_are_hard_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet_path = root / "packets.jsonl"
            packet_path.write_text("{}\n", encoding="utf-8")
            arm = qt4.Arm("a6a", packet_path, root / "run")
            qdir = arm.out_dir / "questions" / "q1"
            qdir.mkdir(parents=True)
            (qdir / "answer.json").write_text("{}\n", encoding="utf-8")
            self.assertEqual(
                qt4._blocking_artifact_reason(
                    arm, "q1", controller_manifest_sha256="current"
                ),
                "orphan_canonical_artifacts",
            )
            (qdir / "answer.json").unlink()
            (qdir / "completion.json").write_text(
                json.dumps(
                    {
                        "status": "answered",
                        "controller_manifest_sha256": "other",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                qt4._blocking_artifact_reason(
                    arm, "q1", controller_manifest_sha256="current"
                ),
                "invalid_or_cross_controller_completion",
            )

    def test_transient_attempt_is_archived_then_clean_retry_can_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet_path = root / "packets.jsonl"
            packet_path.write_text("{}\n", encoding="utf-8")
            arm = qt4.Arm("qt4v", packet_path, root / "run")
            qdir = arm.out_dir / "questions" / "q1"
            qdir.mkdir(parents=True)
            (qdir / "prompt.txt").write_text("prompt one", encoding="utf-8")
            (qdir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 11, "output_tokens": 2},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            first = qt4._write_attempt_receipt(
                arm=arm,
                question_id="q1",
                controller_manifest_sha256="manifest-sha",
                returncode=1,
                attempt_number=1,
            )
            self.assertEqual(
                qt4._failed_attempt_status(first, qdir), "transient_failure"
            )
            archived = qt4._archive_failed_attempt(
                arm=arm,
                question_id="q1",
                receipt={**first, "status": "transient_failure"},
            )
            archive_dir = qdir / "attempts" / "attempt-0001"
            self.assertEqual(archived["usage"]["input_tokens"], 11)
            self.assertTrue((archive_dir / "prompt.txt").exists())
            self.assertTrue((archive_dir / "events.jsonl").exists())
            self.assertTrue((archive_dir / "attempt.json").exists())
            self.assertEqual(
                len((qdir / "attempts.jsonl").read_text().splitlines()), 1
            )
            self.assertIsNone(
                qt4._blocking_artifact_reason(
                    arm, "q1", controller_manifest_sha256="manifest-sha"
                )
            )

            (qdir / "prompt.txt").write_text("prompt two", encoding="utf-8")
            (qdir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 13, "output_tokens": 3},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
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
            second = qt4._write_attempt_receipt(
                arm=arm,
                question_id="q1",
                controller_manifest_sha256="manifest-sha",
                returncode=0,
                attempt_number=2,
            )
            self.assertEqual(second["status"], "answered")
            self.assertTrue(
                qt4.is_terminal_attempt(
                    arm, "q1", controller_manifest_sha256="manifest-sha"
                )
            )
            progress = qt4._progress(
                ["q1"], [arm], controller_manifest_sha256="manifest-sha"
            )
            self.assertEqual(progress["attempts_by_arm"]["qt4v"], 2)
            self.assertEqual(
                progress["archived_token_usage_by_arm"]["qt4v"]["input_tokens"],
                11,
            )
            self.assertEqual(
                progress["accepted_token_usage_by_arm"]["qt4v"]["input_tokens"],
                13,
            )

    def test_retry_cap_is_pinned_and_survives_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet_path = root / "packets.jsonl"
            packet_path.write_text("{}\n", encoding="utf-8")
            arm = qt4.Arm("qt4t", packet_path, root / "run")
            qdir = arm.out_dir / "questions" / "q1"
            qdir.mkdir(parents=True)
            for attempt_number in range(1, qt4.MAX_ATTEMPTS_PER_ITEM + 1):
                (qdir / "prompt.txt").write_text(
                    f"prompt {attempt_number}", encoding="utf-8"
                )
                (qdir / "events.jsonl").write_text(
                    json.dumps({"type": "turn.completed"}) + "\n",
                    encoding="utf-8",
                )
                receipt = qt4._write_attempt_receipt(
                    arm=arm,
                    question_id="q1",
                    controller_manifest_sha256="manifest-sha",
                    returncode=1,
                    attempt_number=attempt_number,
                )
                qt4._archive_failed_attempt(
                    arm=arm,
                    question_id="q1",
                    receipt={**receipt, "status": "transient_failure"},
                )

            self.assertTrue(qt4._retry_cap_reached(arm, "q1"))
            self.assertEqual(len(qt4._attempt_receipts(arm, "q1")), 3)

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
