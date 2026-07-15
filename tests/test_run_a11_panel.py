import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import run_a11_panel as panel
import run_a11_experiment as controller
import codex_harness
import panel_grade
import run_lock
from run_lock import AlreadyRunning, acquire_single_instance


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


class A11PanelTests(unittest.TestCase):
    def make_inputs(self, root: Path) -> dict[str, object]:
        launcher = (root / "codex-launcher.js").resolve()
        launcher.write_bytes(b"sealed-test-launcher")
        launcher.chmod(0o755)
        launcher_sha = hashlib.sha256(launcher.read_bytes()).hexdigest()
        codex = (root / "codex-bin").resolve()
        codex.write_bytes(b"\x7fELFsealed-test-codex")
        codex.chmod(0o755)
        codex_sha = hashlib.sha256(codex.read_bytes()).hexdigest()
        grading = (root / "grading-output").resolve()
        grading.mkdir()
        panel_out = (root / "panel-output").resolve()
        panel_source_sha = hashlib.sha256(Path(panel.__file__).read_bytes()).hexdigest()
        panel_config = {
            "model": panel.REGISTERED_MODEL,
            "reasoning_effort": panel.REGISTERED_REASONING_EFFORT,
            "votes": panel.REGISTERED_VOTES,
            "batch_size": panel.REGISTERED_BATCH_SIZE,
            "timeout_seconds": panel.REGISTERED_TIMEOUT_SECONDS,
            "codex_bin": str(codex),
            "codex_version": "codex-test 1.0",
            "codex_binary_sha256": codex_sha,
            "panel_source_sha256": panel_source_sha,
        }
        controller = root / "controller.json"
        write_json(
            controller,
            {
                "kind": "a11_interleaved_controller_manifest",
                "schema_version": "a11-controller-v2",
                "execution": {
                    "model": panel.REGISTERED_MODEL,
                    "reasoning_effort": panel.REGISTERED_REASONING_EFFORT,
                    "codex": {
                        "path": str(launcher),
                        "version": "codex-test 1.0",
                        "sha256": launcher_sha,
                        "bytes": launcher.stat().st_size,
                        "native": {
                            "path": str(codex),
                            "sha256": codex_sha,
                            "bytes": codex.stat().st_size,
                        },
                    },
                },
                "grading": {"panel": panel_config},
                "outputs": {"grading": str(grading), "panel": str(panel_out)},
                "snapshots": {
                    name: {
                        "snapshot_path": str(path),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "bytes": path.stat().st_size,
                    }
                    for name, path in {
                        "run_a11_panel": Path(panel.__file__).resolve(),
                        "panel_grade": Path(panel_grade.__file__).resolve(),
                        "codex_harness": Path(codex_harness.__file__).resolve(),
                        "run_lock": Path(run_lock.__file__).resolve(),
                    }.items()
                },
            },
        )
        controller_sha = hashlib.sha256(controller.read_bytes()).hexdigest()
        controller.with_suffix(".sha256").write_text(
            controller_sha + "\n", encoding="ascii"
        )
        queue = grading / "panel_queue.jsonl"
        queue.write_text(
            "".join(
                json.dumps(item, sort_keys=True) + "\n"
                for item in (
                    {
                        "arm": "v",
                        "question_id": "secret-q1",
                        "question": "What organism was found?",
                        "gold": {
                            "acceptable_any": ["O-ABC", "Synthetic organism ABC"]
                        },
                        "answer": "Synthetic organism ABC",
                        "insufficiency_reason": None,
                    },
                    {
                        "arm": "e",
                        "question_id": "secret-q2",
                        "question": "What specimen was used?",
                        "gold": {
                            "acceptable_any": ["S-XYZ", "Synthetic sample XYZ"]
                        },
                        "answer": "Synthetic sample XYZ",
                        "insufficiency_reason": None,
                    },
                )
            ),
            encoding="utf-8",
        )
        write_json(
            grading / "manifest.json",
            {
                "schema_version": "a11-grading-preparation-v1",
                "controller_manifest_sha256": controller_sha,
                "model_calls": 0,
                "panel_config": panel_config,
                "all_checks_passed": True,
                "artifacts": {
                    "panel_queue.jsonl": {
                        "sha256": hashlib.sha256(queue.read_bytes()).hexdigest(),
                        "bytes": queue.stat().st_size,
                    }
                },
            },
        )
        return {
            "codex": codex,
            "launcher": launcher,
            "controller": controller,
            "controller_sha": controller_sha,
            "queue": queue,
            "out": panel_out,
        }

    @staticmethod
    def accepted_process(command, **kwargs):
        prompt = kwargs["input"]
        opaque_ids = [
            line["item_id"]
            for line in (
                json.loads(raw)
                for raw in prompt.splitlines()
                if raw.startswith("{") and "item_id" in raw
            )
        ]
        verdict_path = Path(command[command.index("--output-last-message") + 1])
        write_json(
            verdict_path,
            {
                "verdicts": [
                    {"item_id": opaque_id, "correct": True}
                    for opaque_id in opaque_ids
                ]
            },
        )
        kwargs["stdout"].write(
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 25,
                        "output_tokens": 10,
                        "reasoning_output_tokens": 4,
                        "total_tokens": 110,
                    },
                }
            )
            + "\n"
        )
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=0)

    @staticmethod
    def failed_process(_command, **kwargs):
        kwargs["stdout"].write(
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 5,
                        "total_tokens": 25,
                    },
                }
            )
            + "\n"
        )
        kwargs["stderr"].write("transport diagnostic\n")
        kwargs["stdout"].flush()
        kwargs["stderr"].flush()
        return SimpleNamespace(returncode=1)

    @staticmethod
    def provider_failure_process(_command, **kwargs):
        message = "Synthetic provider unavailable"
        for event in (
            {"type": "thread.started", "thread_id": "synthetic-thread"},
            {"type": "turn.started"},
            {"type": "error", "message": message},
            {"type": "turn.failed", "error": {"message": message}},
        ):
            kwargs["stdout"].write(json.dumps(event) + "\n")
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=1)

    def run_live(self, paths, process):
        with mock.patch.object(panel, "codex_version", return_value="codex-test 1.0"):
            return panel.run_panel(
                queue_path=paths["queue"],
                controller_manifest=paths["controller"],
                expected_controller_sha256=paths["controller_sha"],
                out_dir=paths["out"],
                live=True,
                run_process=process,
            )

    def test_acceptable_any_prompt_is_opaque_and_config_is_fully_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            queue_bytes, queue = panel.load_a11_queue(paths["queue"])
            codex = panel.CodexIdentity(
                path=paths["codex"],
                version="codex-test 1.0",
                sha256=hashlib.sha256(paths["codex"].read_bytes()).hexdigest(),
            )
            config = panel.build_judge_config(
                controller_manifest_sha256=paths["controller_sha"], codex=codex
            )
            blinded = panel.prepare_blinded_items(queue, config)
            prompt = panel.batch_prompt(blinded)

            self.assertEqual(config["model"], "gpt-5.6-sol")
            self.assertEqual(config["reasoning_effort"], "high")
            self.assertEqual(config["requested_votes"], 3)
            self.assertEqual(config["batch_size"], 20)
            self.assertEqual(config["timeout_seconds"], 600)
            self.assertEqual(config["max_operational_attempts_per_batch"], 3)
            self.assertTrue(config["empty_nonrepository_cwd"])
            self.assertFalse(config["tool_events_allowed"])
            self.assertEqual(config["codex_binary"], str(paths["codex"]))
            self.assertEqual(queue_bytes, paths["queue"].read_bytes())
            self.assertIn("acceptable_any", prompt)
            self.assertNotIn("secret-q1", prompt)
            self.assertNotIn("secret-q2", prompt)
            self.assertNotIn('"arm"', prompt)
            for item in blinded:
                self.assertRegex(item["opaque_id"], r"^a11panel_[0-9a-f]{32}$")
                self.assertIn(item["opaque_id"], prompt)

    def test_queue_rejects_unregistered_null_gold_panel_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            rows = [json.loads(line) for line in paths["queue"].read_text().splitlines()]
            rows[0]["gold"] = None
            paths["queue"].write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "acceptable_any"):
                panel.load_a11_queue(paths["queue"])

    def test_live_run_persists_separate_streams_and_bound_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            result = self.run_live(paths, self.accepted_process)

            self.assertEqual(result["status"], "complete")
            receipts = sorted(paths["out"].glob("attempts/**/receipt.json"))
            self.assertEqual(len(receipts), 3)
            for receipt_path in receipts:
                attempt_dir = receipt_path.parent
                receipt = json.loads(receipt_path.read_text())
                self.assertEqual(receipt["status"], "accepted")
                self.assertEqual(
                    receipt["controller_manifest_sha256"], paths["controller_sha"]
                )
                self.assertEqual(
                    receipt["queue_sha256"],
                    hashlib.sha256(paths["queue"].read_bytes()).hexdigest(),
                )
                self.assertEqual(
                    receipt["judge_config_sha256"],
                    json.loads((paths["out"] / "manifest.json").read_text())[
                        "judge_config_sha256"
                    ],
                )
                self.assertTrue((attempt_dir / "events.jsonl").read_text())
                self.assertEqual((attempt_dir / "stderr.log").read_bytes(), b"")
                self.assertEqual(
                    receipt["event_stream"]["sha256"],
                    hashlib.sha256((attempt_dir / "events.jsonl").read_bytes()).hexdigest(),
                )
                self.assertEqual(
                    receipt["stderr"]["sha256"],
                    hashlib.sha256(b"").hexdigest(),
                )
                self.assertIsNotNone(receipt["verdicts_sha256"])
                self.assertTrue(receipt["usage"]["complete"])

            final = json.loads((paths["out"] / "panel_verdicts.json").read_text())
            self.assertEqual(set(final), {"e|secret-q2", "v|secret-q1"})

    def test_failed_attempt_cap_is_persisted_across_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            for expected_attempt in range(1, 4):
                result = self.run_live(paths, self.provider_failure_process)
                self.assertEqual(result["status"], "operational_attempt_failed")
                self.assertEqual(result["attempt_number"], expected_attempt)

            calls = 0

            def should_not_run(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                raise AssertionError("retry cap should prevent another model call")

            with self.assertRaisesRegex(panel.PanelProtocolError, "retry cap"):
                self.run_live(paths, should_not_run)
            self.assertEqual(calls, 0)
            receipts = sorted(paths["out"].glob("attempts/**/receipt.json"))
            self.assertEqual(len(receipts), 3)
            self.assertTrue(
                all(json.loads(path.read_text())["status"] == "failed" for path in receipts)
            )
            self.assertTrue(
                all(
                    json.loads(path.read_text())["retryable_provider_failure"] is True
                    for path in receipts
                )
            )

    def test_completed_malformed_vote_is_never_resampled(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            calls = 0

            def malformed(command, **kwargs):
                nonlocal calls
                calls += 1
                verdict_path = Path(command[command.index("--output-last-message") + 1])
                write_json(verdict_path, {"verdicts": []})
                kwargs["stdout"].write(
                    json.dumps(
                        {
                            "type": "turn.completed",
                            "usage": {
                                "input_tokens": 20,
                                "output_tokens": 5,
                                "total_tokens": 25,
                            },
                        }
                    )
                    + "\n"
                )
                kwargs["stdout"].flush()
                return SimpleNamespace(returncode=0)

            with self.assertRaisesRegex(panel.PanelProtocolError, "nonretryable"):
                self.run_live(paths, malformed)
            self.assertEqual(calls, 1)
            with self.assertRaisesRegex(panel.PanelProtocolError, "nonretryable"):
                self.run_live(paths, malformed)
            self.assertEqual(calls, 1)

    def test_tool_event_invalidates_panel_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))

            def tool_using(command, **kwargs):
                result = self.accepted_process(command, **kwargs)
                kwargs["stdout"].write(
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {"type": "command_execution", "id": "synthetic-tool"},
                        }
                    )
                    + "\n"
                )
                kwargs["stdout"].flush()
                return result

            with self.assertRaisesRegex(panel.PanelProtocolError, "nonretryable"):
                self.run_live(paths, tool_using)
            receipt = json.loads(
                next(paths["out"].glob("attempts/**/receipt.json")).read_text()
            )
            self.assertFalse(receipt["retryable_provider_failure"])
            self.assertTrue(
                receipt["event_integrity"]["codex_event_audit"]["contaminated"]
            )

    def test_accepted_vote_is_immutable_and_never_recalled(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            self.run_live(paths, self.accepted_process)
            verdict = next(paths["out"].glob("attempts/**/verdict.json"))
            verdict.chmod(0o644)
            verdict.write_text('{"verdicts": []}\n', encoding="utf-8")

            with self.assertRaisesRegex(panel.PanelProtocolError, "artifact changed"):
                self.run_live(paths, self.accepted_process)

    def test_audit_replays_attempts_and_derives_majority(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            self.run_live(paths, self.accepted_process)
            with mock.patch.object(panel, "codex_version", return_value="codex-test 1.0"):
                audit = panel.audit_completed_panel(
                    queue_path=paths["queue"],
                    controller_manifest=paths["controller"],
                    expected_controller_sha256=paths["controller_sha"],
                    out_dir=paths["out"],
                )
            self.assertTrue(audit["all_checks_passed"])
            self.assertEqual(audit["votes_per_item"], 3)
            self.assertEqual(
                audit["verdicts"], {"e|secret-q2": 1, "v|secret-q1": 1}
            )

    def test_audit_rejects_unregistered_attempt_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            self.run_live(paths, self.accepted_process)
            paths["out"].chmod(0o755)
            attempts = paths["out"] / "attempts"
            attempts.chmod(0o755)
            unregistered = attempts / "unregistered"
            unregistered.mkdir()
            write_json(unregistered / "receipt.json", {"status": "accepted"})
            with mock.patch.object(panel, "codex_version", return_value="codex-test 1.0"):
                with self.assertRaisesRegex(
                    panel.PanelProtocolError, "unregistered receipt"
                ):
                    panel.audit_completed_panel(
                        queue_path=paths["queue"],
                        controller_manifest=paths["controller"],
                        expected_controller_sha256=paths["controller_sha"],
                        out_dir=paths["out"],
                    )

    def test_binary_sha_is_rechecked_before_and_after_each_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            calls = 0

            def mutate_after_first(command, **kwargs):
                nonlocal calls
                calls += 1
                result = self.accepted_process(command, **kwargs)
                if calls == 1:
                    paths["codex"].write_bytes(b"mutated-binary")
                return result

            with self.assertRaisesRegex(panel.PanelProtocolError, "hard stop"):
                self.run_live(paths, mutate_after_first)
            self.assertEqual(calls, 1)
            receipts = sorted(paths["out"].glob("attempts/**/receipt.json"))
            self.assertEqual(len(receipts), 1)
            receipt = json.loads(receipts[0].read_text())
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["error"], "codex_binary_changed_after_call")

    def test_singleton_lock_prevents_duplicate_panel_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            with acquire_single_instance(panel.panel_lock_path(paths["out"])):
                with self.assertRaises(AlreadyRunning):
                    self.run_live(paths, self.accepted_process)

    def test_registered_output_and_grading_queue_cannot_be_substituted(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            with mock.patch.object(panel, "codex_version", return_value="codex-test 1.0"):
                with self.assertRaisesRegex(ValueError, "registered panel output"):
                    panel.run_panel(
                        queue_path=paths["queue"],
                        controller_manifest=paths["controller"],
                        expected_controller_sha256=paths["controller_sha"],
                        out_dir=Path(tmp) / "substitute-output",
                        live=True,
                        run_process=self.accepted_process,
                    )

            grading_manifest = paths["queue"].parent / "manifest.json"
            manifest = json.loads(grading_manifest.read_text())
            manifest["artifacts"]["panel_queue.jsonl"]["sha256"] = "0" * 64
            write_json(grading_manifest, manifest)
            with self.assertRaisesRegex(panel.PanelProtocolError, "queue binding"):
                self.run_live(paths, self.accepted_process)

    def test_controller_identity_rejects_model_and_binary_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            with mock.patch.object(
                panel, "codex_version", return_value="codex-test 1.0"
            ) as version:
                controller_sha, codex, panel_output = panel.load_controller_codex_identity(
                    paths["controller"],
                    expected_controller_sha256=paths["controller_sha"],
                )
            self.assertEqual(controller_sha, paths["controller_sha"])
            self.assertEqual(codex.path, paths["codex"])
            self.assertEqual(panel_output, paths["out"])
            version.assert_called_once_with(paths["codex"])

            paths["codex"].write_bytes(b"changed")
            with mock.patch.object(panel, "codex_version", return_value="codex-test 1.0"):
                with self.assertRaisesRegex(ValueError, "identity changed"):
                    panel.load_controller_codex_identity(
                        paths["controller"],
                        expected_controller_sha256=paths["controller_sha"],
                    )

    def test_controller_identity_keeps_strict_legacy_v1_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            manifest = json.loads(paths["controller"].read_text())
            manifest["schema_version"] = "a11-controller-v1"
            launcher = manifest["execution"]["codex"]
            manifest["execution"]["codex"] = {
                "path": launcher["path"],
                "version": "codex-test 1.0",
                "sha256": launcher["sha256"],
            }
            manifest["grading"]["panel"]["codex_bin"] = launcher["path"]
            manifest["grading"]["panel"]["codex_binary_sha256"] = launcher[
                "sha256"
            ]
            write_json(paths["controller"], manifest)
            controller_sha = hashlib.sha256(
                paths["controller"].read_bytes()
            ).hexdigest()
            paths["controller"].with_suffix(".sha256").write_text(
                controller_sha + "\n", encoding="ascii"
            )

            with mock.patch.object(
                panel, "codex_version", return_value="codex-test 1.0"
            ):
                loaded_sha, codex, _ = panel.load_controller_codex_identity(
                    paths["controller"],
                    expected_controller_sha256=controller_sha,
                )
            self.assertEqual(loaded_sha, controller_sha)
            self.assertEqual(codex.path, paths["launcher"])

            bundle = SimpleNamespace(
                manifest={
                    "schema_version": "a11-controller-v1",
                    "execution": {
                        "codex": manifest["execution"]["codex"],
                        "python_path": "/synthetic/python",
                    },
                },
                input_path=Path("/synthetic/input.csv"),
                schema_path=Path("/synthetic/schema.json"),
                harness_path=Path("/synthetic/a11_answer_harness.py"),
                prompt_by_host={
                    ("q1", "v"): {
                        "model_payload_sha256": "1" * 64,
                        "model_payload_utf8_bytes": 10,
                        "prompt_sha256": "2" * 64,
                        "prompt_utf8_bytes": 20,
                    }
                },
            )
            arm = SimpleNamespace(
                name="v",
                packet_path=Path("/synthetic/v.jsonl"),
                out_dir=Path("/synthetic/out-v"),
            )
            receipt = controller._a11_receipt_fields(
                bundle=bundle,
                arm=arm,
                question_id="q1",
            )
            command = controller._build_a11_harness_command(
                bundle=bundle,
                arm=arm,
                question_id="q1",
            )
            self.assertEqual(receipt["codex_binary_sha256"], launcher["sha256"])
            self.assertEqual(
                command[command.index("--codex-bin") + 1], launcher["path"]
            )

    def test_seal_runtime_binding_is_accepted_by_answer_and_panel_consumers(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            manifest = json.loads(paths["controller"].read_text())
            sealed_identity = manifest["execution"]["codex"]
            schema = Path(tmp) / "answer-schema.json"
            schema.write_text("{}\n", encoding="utf-8")
            code_sources = {
                "run_a11_panel": Path(panel.__file__).resolve(),
                "a11_grading": Path(controller.__file__).with_name(
                    "a11_grading.py"
                ),
            }
            with mock.patch.object(
                controller,
                "_codex_identity",
                return_value=sealed_identity,
            ):
                produced_identity, analysis = controller._registered_codex_analysis(
                    codex_bin=str(paths["launcher"]),
                    schema_path=schema,
                    code_sources=code_sources,
                )

            manifest["execution"]["codex"] = produced_identity
            manifest["grading"] = analysis
            write_json(paths["controller"], manifest)
            controller_sha = hashlib.sha256(
                paths["controller"].read_bytes()
            ).hexdigest()
            paths["controller"].with_suffix(".sha256").write_text(
                controller_sha + "\n", encoding="ascii"
            )

            runtime = controller._codex_runtime(produced_identity)
            self.assertEqual(analysis["panel"]["codex_bin"], runtime["path"])
            self.assertEqual(
                analysis["panel"]["codex_binary_sha256"], runtime["sha256"]
            )
            with mock.patch.object(
                panel, "codex_version", return_value=runtime["version"]
            ):
                _, panel_codex, _ = panel.load_controller_codex_identity(
                    paths["controller"],
                    expected_controller_sha256=controller_sha,
                )
            self.assertEqual(str(panel_codex.path), runtime["path"])
            self.assertEqual(panel_codex.sha256, runtime["sha256"])

    def test_controller_identity_rejects_launcher_drift_before_version_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            paths["launcher"].write_bytes(b"drifted-test-launcher")
            with (
                mock.patch.object(
                    panel,
                    "codex_version",
                    side_effect=AssertionError(
                        "drifted launcher must fail before executing Codex"
                    ),
                ),
                self.assertRaisesRegex(ValueError, "identity changed"),
            ):
                panel.load_controller_codex_identity(
                    paths["controller"],
                    expected_controller_sha256=paths["controller_sha"],
                )

    def test_controller_identity_rejects_loaded_helper_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.make_inputs(Path(tmp))
            manifest = json.loads(paths["controller"].read_text())
            manifest["snapshots"]["panel_grade"]["sha256"] = "0" * 64
            write_json(paths["controller"], manifest)
            changed_sha = hashlib.sha256(paths["controller"].read_bytes()).hexdigest()
            paths["controller"].with_suffix(".sha256").write_text(
                changed_sha + "\n", encoding="ascii"
            )
            with mock.patch.object(panel, "codex_version", return_value="codex-test 1.0"):
                with self.assertRaisesRegex(ValueError, "runtime differs"):
                    panel.load_controller_codex_identity(
                        paths["controller"],
                        expected_controller_sha256=changed_sha,
                    )


if __name__ == "__main__":
    unittest.main()
