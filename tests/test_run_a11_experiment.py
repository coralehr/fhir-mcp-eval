from __future__ import annotations

import csv
import hashlib
import json
import platform
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import run_a11_experiment as controller


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


class A11ControllerTests(unittest.TestCase):
    def test_early_controller_hashes_and_parses_one_byte_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            first = {
                "kind": "a11_interleaved_controller_manifest",
                "schema_version": "a11-controller-v2",
                "marker": "first",
            }
            second = {**first, "marker": "swapped"}
            first_bytes = (json.dumps(first, sort_keys=True) + "\n").encode()
            path.write_bytes(first_bytes)
            path.with_suffix(".sha256").write_text(
                hashlib.sha256(first_bytes).hexdigest() + "\n", encoding="ascii"
            )
            real_hash = controller._early_sha256

            def swap_after_hash(target: Path) -> str:
                digest = real_hash(target)
                target.write_text(json.dumps(second) + "\n", encoding="utf-8")
                return digest

            with (
                mock.patch.object(sys, "argv", ["runner", "--controller-manifest", str(path)]),
                mock.patch.object(
                    controller, "_early_sha256", side_effect=swap_after_hash
                ),
            ):
                _path, manifest, digest = controller._early_controller()
            self.assertEqual(manifest["marker"], "first")
            self.assertEqual(digest, hashlib.sha256(first_bytes).hexdigest())

    def test_controller_reader_hashes_and_parses_one_byte_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            first = {"marker": "first"}
            second = {"marker": "swapped"}
            first_bytes = (json.dumps(first, sort_keys=True) + "\n").encode()
            path.write_bytes(first_bytes)
            path.with_suffix(".sha256").write_text(
                hashlib.sha256(first_bytes).hexdigest() + "\n", encoding="ascii"
            )
            real_hash = controller._sha256_file

            def swap_after_hash(target: Path) -> str:
                digest = real_hash(target)
                target.write_text(json.dumps(second) + "\n", encoding="utf-8")
                return digest

            with mock.patch.object(
                controller, "_sha256_file", side_effect=swap_after_hash
            ):
                digest, manifest = controller._read_controller_manifest_once(path)
            self.assertEqual(manifest["marker"], "first")
            self.assertEqual(digest, hashlib.sha256(first_bytes).hexdigest())

    def _bootstrap_manifest(
        self, root: Path, version: str, *, include_anchor: bool
    ) -> dict:
        names = controller._BOOTSTRAP_SNAPSHOTS
        snapshots = {}
        for name in names:
            if name == "experiment_anchor" and not include_anchor:
                continue
            source = root / f"source-{name}.py"
            payload = f"# sealed {name}\n".encode()
            source.write_bytes(payload)
            source.chmod(0o444)
            snapshots[name] = {
                "snapshot_path": str(source.resolve()),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        return {"schema_version": version, "snapshots": snapshots}

    def test_legacy_v2_bootstrap_stages_and_verifies_without_anchor_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller_path = root / "manifest.json"
            manifest = self._bootstrap_manifest(
                root, "a11-controller-v2", include_anchor=False
            )

            runner = controller._stage_bootstrap(
                controller_path, manifest, "a" * 64
            )

            self.assertEqual(runner, root / "bootstrap" / "run_a11_experiment.py")
            self.assertFalse((root / "bootstrap" / "experiment_anchor.py").exists())
            self.assertEqual(
                controller._verify_bootstrap(
                    root / "bootstrap",
                    controller_sha256="a" * 64,
                    bootstrap_snapshots=controller._LEGACY_BOOTSTRAP_SNAPSHOTS,
                ),
                runner,
            )

    def test_v3_bootstrap_fails_closed_without_anchor_helper_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._bootstrap_manifest(
                root, "a11-controller-v3", include_anchor=False
            )
            with self.assertRaisesRegex(SystemExit, "experiment_anchor"):
                controller._stage_bootstrap(
                    root / "manifest.json", manifest, "b" * 64
                )

    def test_publish_controller_seal_rolls_back_after_anchor_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            snapshot = artifact_dir / "snapshot.bin"
            snapshot.write_bytes(b"sealed")
            snapshot.chmod(0o444)
            artifact_dir.chmod(0o555)

            with (
                mock.patch.object(
                    controller.experiment_anchor,
                    "write_anchor_request",
                    side_effect=OSError("synthetic anchor write failure"),
                ),
                self.assertRaisesRegex(OSError, "synthetic anchor"),
            ):
                controller._publish_controller_seal(
                    controller_manifest=manifest_path,
                    manifest={"schema_version": "a11-controller-v3"},
                    artifact_dir=artifact_dir,
                )

            self.assertFalse(manifest_path.exists())
            self.assertFalse(manifest_path.with_suffix(".sha256").exists())
            self.assertFalse(artifact_dir.exists())

    def test_publish_controller_seal_rolls_back_after_controller_fsync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            (artifact_dir / "snapshot.bin").write_bytes(b"sealed")
            artifact_dir.chmod(0o555)
            with (
                mock.patch.object(
                    controller.os,
                    "fsync",
                    side_effect=OSError("synthetic controller fsync failure"),
                ),
                self.assertRaisesRegex(OSError, "controller fsync"),
            ):
                controller._publish_controller_seal(
                    controller_manifest=manifest_path,
                    manifest={"schema_version": "a11-controller-v3"},
                    artifact_dir=artifact_dir,
                )
            self.assertFalse(manifest_path.exists())
            self.assertFalse(artifact_dir.exists())

    def test_controller_seal_preflights_anchor_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manifest.json"
            manifest_path.with_name("anchor-request.json").write_text(
                "stale\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(FileExistsError, "anchor request"):
                controller._preflight_controller_seal(manifest_path)

    def test_legacy_bootstrap_remains_readable_but_live_is_audit_only(self) -> None:
        legacy = controller._bootstrap_snapshots(
            {"schema_version": "a11-controller-v2"}
        )
        current = controller._bootstrap_snapshots(
            {"schema_version": "a11-controller-v3"}
        )
        self.assertNotIn("experiment_anchor", legacy)
        self.assertIn("experiment_anchor", current)

        bundle = SimpleNamespace(
            manifest={"schema_version": "a11-controller-v2"},
        )
        with (
            mock.patch.object(
                controller,
                "_verify_loaded_code",
                side_effect=AssertionError("legacy rejection must happen first"),
            ),
            self.assertRaisesRegex(ValueError, "audit-only"),
        ):
            controller.run_live(
                bundle,
                lock_path=Path("/synthetic/legacy.lock"),
                max_attempts=None,
                anchor_url="https://example.invalid/anchor",
            )

    def test_live_requires_external_anchor_locator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller_path = Path(directory) / "manifest.json"
            controller_path.write_text("{}\n", encoding="utf-8")
            with (
                mock.patch.object(controller, "load_controller") as load_controller,
                mock.patch.object(controller, "run_live") as run_live,
                self.assertRaisesRegex(SystemExit, "--anchor-url"),
            ):
                controller.main(
                    [
                        "--controller-manifest",
                        str(controller_path),
                        "--live",
                    ]
                )
            load_controller.assert_not_called()
            run_live.assert_not_called()

    def test_live_verifies_external_anchor_before_lock_or_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            lock_path = root / "a11.lock"
            bundle = SimpleNamespace(
                manifest_path=manifest_path,
                manifest_sha256="a" * 64,
                manifest={
                    "schema_version": "a11-controller-v3",
                    "integrity": {"singleton_lock": str(lock_path.resolve())},
                    "execution": {
                        "model": controller.REGISTERED_MODEL,
                        "reasoning_effort": controller.REGISTERED_REASONING_EFFORT,
                    },
                },
            )
            with (
                mock.patch.object(controller, "_verify_loaded_code"),
                mock.patch.object(
                    controller.experiment_anchor,
                    "verify_and_record_external_anchor",
                    side_effect=ValueError("external anchor missing"),
                ) as verify_anchor,
                mock.patch.object(
                    controller.transport,
                    "_acquire_live_instance_lock",
                    side_effect=AssertionError("lock must follow external anchor"),
                ),
                self.assertRaisesRegex(ValueError, "external anchor missing"),
            ):
                controller.run_live(
                    bundle,
                    lock_path=lock_path,
                    max_attempts=None,
                    anchor_url="https://example.invalid/anchor",
                )
            verify_anchor.assert_called_once_with(
                manifest_path,
                "https://example.invalid/anchor",
                manifest_path.with_name("external-anchor-verification.json"),
                expected_controller_sha256=bundle.manifest_sha256,
            )

    def test_codex_identity_rejects_executable_wrapper_indirection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            wrapper = Path(directory) / "codex"
            wrapper.write_text(
                "#!/bin/sh\nexec codex-real \"$@\"\n",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)

            with (
                mock.patch.object(
                    controller.codex_harness.subprocess,
                    "run",
                    return_value=SimpleNamespace(
                        returncode=0,
                        stdout="codex-cli 1.2.3\n",
                        stderr="",
                    ),
                ),
                self.assertRaisesRegex(ValueError, "native executable"),
            ):
                controller._codex_identity(str(wrapper))

    def test_codex_identity_rejects_binary_changed_during_version_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "codex"
            binary.write_bytes(b"\x7fELFsealed native codex v1")
            binary.chmod(0o755)

            def mutate_binary(*_args, **_kwargs):
                binary.write_bytes(b"\x7fELFsealed native codex v2")
                return SimpleNamespace(
                    returncode=0,
                    stdout="codex-cli 1.2.3\n",
                    stderr="",
                )

            with (
                mock.patch.object(
                    controller.codex_harness.subprocess,
                    "run",
                    side_effect=mutate_binary,
                ),
                self.assertRaisesRegex(ValueError, "changed during version probe"),
            ):
                controller._codex_identity(str(binary))

    def test_codex_identity_rejects_nonzero_version_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "codex"
            binary.write_bytes(b"\x7fELFsealed native codex")
            binary.chmod(0o755)

            with (
                mock.patch.object(
                    controller.codex_harness.subprocess,
                    "run",
                    return_value=SimpleNamespace(
                        returncode=1,
                        stdout="",
                        stderr="probe failed\n",
                    ),
                ),
                self.assertRaisesRegex(ValueError, "version probe failed"),
            ):
                controller._codex_identity(str(binary))

    def test_direct_native_codex_identity_hashes_the_binary_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "codex"
            binary.write_bytes(b"\x7fELFnative codex")
            binary.chmod(0o755)
            with (
                mock.patch.object(
                    controller.codex_harness,
                    "strict_codex_version",
                    return_value="codex-cli 1.2.3",
                ),
                mock.patch.object(
                    controller.codex_harness,
                    "sha256_file",
                    wraps=controller.codex_harness.sha256_file,
                ) as sha256_file,
            ):
                identity = controller._codex_identity(str(binary))

            self.assertEqual(identity["path"], identity["native"]["path"])
            self.assertEqual(identity["sha256"], identity["native"]["sha256"])
            self.assertEqual(sha256_file.call_count, 1)

    def test_codex_identity_binds_native_executable_behind_js_launcher(self) -> None:
        target = {
            ("darwin", "arm64"): (
                "aarch64-apple-darwin",
                "@openai/codex-darwin-arm64",
                "codex",
            ),
            ("darwin", "x86_64"): (
                "x86_64-apple-darwin",
                "@openai/codex-darwin-x64",
                "codex",
            ),
            ("linux", "x86_64"): (
                "x86_64-unknown-linux-musl",
                "@openai/codex-linux-x64",
                "codex",
            ),
            ("linux", "aarch64"): (
                "aarch64-unknown-linux-musl",
                "@openai/codex-linux-arm64",
                "codex",
            ),
        }.get((sys.platform, platform.machine().lower()))
        if target is None:
            self.skipTest("Codex test fixture has no platform package mapping")
        target_triple, platform_package, executable_name = target

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package_root = root / "node_modules/@openai/codex"
            launcher = package_root / "bin/codex.js"
            native = (
                package_root
                / "node_modules"
                / platform_package
                / "vendor"
                / target_triple
                / "bin"
                / executable_name
            )
            launcher.parent.mkdir(parents=True)
            native.parent.mkdir(parents=True)
            launcher.write_text("#!/usr/bin/env node\n", encoding="utf-8")
            native.write_bytes(b"\x7fELFsealed native codex v1")
            launcher.chmod(0o755)
            native.chmod(0o755)

            with mock.patch.object(
                controller.codex_harness,
                "strict_codex_version",
                return_value="codex-cli 1.2.3",
            ):
                identity = controller._codex_identity(str(launcher))

            self.assertEqual(identity["path"], str(launcher.resolve()))
            self.assertEqual(identity["bytes"], launcher.stat().st_size)
            self.assertEqual(
                identity["native"],
                {
                    "path": str(native.resolve()),
                    "bytes": native.stat().st_size,
                    "sha256": controller._sha256_file(native),
                },
            )

            native.write_bytes(b"\x7fELFsealed native codex v2")
            with (
                mock.patch.object(
                    controller.codex_harness,
                    "strict_codex_version",
                    side_effect=AssertionError(
                        "drifted executable must not run during verification"
                    ),
                ),
                self.assertRaisesRegex(ValueError, "identity changed"),
            ):
                controller.verify_codex_identity(identity)

    def test_js_launcher_without_native_binary_fails_before_version_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory) / "node_modules/@openai/codex/bin/codex.js"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/usr/bin/env node\n", encoding="utf-8")
            launcher.chmod(0o755)

            with (
                mock.patch.object(
                    controller.codex_harness,
                    "strict_codex_version",
                    side_effect=AssertionError(
                        "missing native executable must fail before version call"
                    ),
                ),
                self.assertRaisesRegex(ValueError, "native executable"),
            ):
                controller._codex_identity(str(launcher))

    def test_answer_receipt_uses_normalized_native_runtime(self) -> None:
        identity = {
            "path": "/synthetic/codex.js",
            "version": "codex-cli 1.2.3",
            "sha256": "1" * 64,
            "bytes": 10,
            "native": {
                "path": "/synthetic/native/codex",
                "sha256": "2" * 64,
                "bytes": 20,
            },
        }
        bundle = SimpleNamespace(
            manifest={
                "schema_version": "a11-controller-v2",
                "execution": {
                    "codex": identity,
                    "python_path": "/synthetic/python",
                },
            },
            input_path=Path("/synthetic/input.csv"),
            schema_path=Path("/synthetic/schema.json"),
            harness_path=Path("/synthetic/a11_answer_harness.py"),
            prompt_by_host={
                ("q1", "v"): {
                    "model_payload_sha256": "3" * 64,
                    "model_payload_utf8_bytes": 30,
                    "prompt_sha256": "4" * 64,
                    "prompt_utf8_bytes": 40,
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
        self.assertEqual(receipt["codex_binary_sha256"], "2" * 64)
        self.assertEqual(
            controller._codex_runtime(identity)["path"],
            "/synthetic/native/codex",
        )
        command = controller._build_a11_harness_command(
            bundle=bundle,
            arm=arm,
            question_id="q1",
        )
        self.assertEqual(
            command[command.index("--codex-bin") + 1],
            "/synthetic/native/codex",
        )

    def test_answer_command_rejects_post_call_native_binary_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "codex"
            binary.write_bytes(b"\x7fELFsealed answer binary v1")
            binary.chmod(0o755)
            receipt = controller.codex_harness.executable_receipt(binary)
            bundle = SimpleNamespace(
                manifest={
                    "execution": {
                        "codex": {
                            **receipt,
                            "version": "codex-cli 1.2.3",
                            "native": receipt,
                        }
                    }
                }
            )

            def mutate_binary(_command, **_kwargs):
                binary.write_bytes(b"\x7fELFsealed answer binary v2")
                return SimpleNamespace(returncode=0)

            result, runtime_unchanged = controller._execute_a11_harness_command(
                bundle=bundle,
                command=[str(binary)],
                run_process=mutate_binary,
            )

            self.assertEqual(result.returncode, 0)
            self.assertFalse(runtime_unchanged)

    def test_registered_execution_and_analysis_are_frozen(self) -> None:
        self.assertEqual(controller.CONTROLLER_VERSION, "a11-controller-v3")
        self.assertEqual(controller.REGISTERED_MODEL, "gpt-5.6-sol")
        self.assertEqual(controller.REGISTERED_REASONING_EFFORT, "high")
        self.assertEqual(controller.REGISTERED_TIMEOUT_SECONDS, 600)
        self.assertEqual(controller.REGISTERED_MAX_ATTEMPTS, 3)
        self.assertEqual(controller.ARMS, ("v", "t", "e"))
        self.assertEqual(
            controller.REGISTERED_ANALYSIS_ORDER[:3],
            (
                "hard_failures",
                "primary_e_minus_t_all_efficacy",
                "secondary_t_minus_v_answerable",
            ),
        )

    def test_prompt_records_are_exact_blind_and_arm_envelope_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                {
                    "question_id": "a11q-one",
                    "question": "What synthetic organism was found?",
                    "assumption": "Synthetic non-PHI data.",
                },
                {
                    "question_id": "a11q-two",
                    "question": "What synthetic specimen was used?",
                    "assumption": "Synthetic non-PHI data.",
                },
            ]
            with (root / "answer_input.csv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            payloads = {
                "v": '{\n  "resources": []\n}',
                "t": '{"resources":[],"path_citations":[]}',
                "e": '{"event_groups":[],"answerability_receipt":{"state":"insufficient"}}',
            }
            for arm, payload in payloads.items():
                encoded = payload.encode()
                _write_jsonl(
                    root / f"{arm}_packets.jsonl",
                    [
                        {
                            "question_id": row["question_id"],
                            "model_payload_json": payload,
                            "model_payload_sha256": controller._sha256_bytes(encoded),
                            "model_payload_utf8_bytes": len(encoded),
                        }
                        for row in rows
                    ],
                )

            records, prompt_index, question_ids = controller.build_prompt_records(
                answer_inputs_dir=root
            )

            self.assertEqual(question_ids, ("a11q-one", "a11q-two"))
            self.assertEqual(len(prompt_index), 6)
            self.assertEqual(set(records), {"v", "t", "e"})
            for arm in controller.ARMS:
                decoded = [json.loads(line) for line in records[arm].splitlines()]
                self.assertEqual(len(decoded), 2)
                for record in decoded:
                    prompt = record["prompt_text"].encode()
                    self.assertEqual(
                        record["prompt_sha256"], controller._sha256_bytes(prompt)
                    )
                    self.assertNotIn(b"patient_fhir_id", prompt)
                    self.assertNotIn(b"Arm:", prompt)
                    self.assertIn(
                        record["model_payload_json"].encode(),
                        prompt,
                    )

    def test_strict_usage_requires_one_integer_reconciled_completed_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 10,
                            "cached_input_tokens": 3,
                            "output_tokens": 4,
                            "reasoning_output_tokens": 2,
                        },
                    }
                )
                + "\n"
            )
            receipt = controller.strict_event_usage(path)
            self.assertEqual(receipt["total_tokens"], 14)
            self.assertEqual(
                receipt["total_tokens_source"], "derived_input_plus_output"
            )
            self.assertTrue(receipt["cached_input_tokens_complete"])

            path.write_text(path.read_text() + path.read_text())
            with self.assertRaisesRegex(ValueError, "exactly one"):
                controller.strict_event_usage(path)

            path.write_text(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 4,
                            "total_tokens": 15,
                        },
                    }
                )
                + "\n"
            )
            with self.assertRaisesRegex(ValueError, "reconcile"):
                controller.strict_event_usage(path)

    def test_only_exact_answerless_provider_failure_is_retryable(self) -> None:
        provider_audit = {
            "contaminated": True,
            "event_log_exists": True,
            "findings": [],
            "parse_error_lines": [],
            "integrity_errors": ["turn_completed_missing"],
            "event_count": 4,
            "turn_completed_count": 0,
            "thread_started_count": 1,
            "turn_started_count": 1,
            "error_event_count": 1,
            "turn_failed_count": 1,
            "item_event_count": 0,
            "event_type_sequence": [
                "thread_started",
                "turn_started",
                "error",
                "turn_failed",
            ],
            "utf8_valid": True,
            "terminal_newline": True,
            "provider_failure_shape": True,
        }
        receipt = {
            "status": "invalid",
            "harness_exit_code": 1,
            "answer_sha256": None,
            "event_integrity": provider_audit,
        }
        self.assertTrue(controller.is_a11_retryable_provider_failure(receipt))
        self.assertFalse(
            controller.is_a11_retryable_provider_failure(
                {
                    **receipt,
                    "event_integrity": {
                        **provider_audit,
                        "contaminated": False,
                        "integrity_errors": [],
                        "turn_completed_count": 1,
                    },
                }
            )
        )
        self.assertFalse(
            controller.is_a11_retryable_provider_failure(
                {**receipt, "answer_sha256": "a" * 64}
            )
        )

    def test_status_and_live_refuse_to_create_a_missing_controller(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "manifest.json"
            for mode in ("--status", "--live"):
                with self.subTest(mode=mode):
                    with self.assertRaisesRegex(
                        SystemExit, "not sealed"
                    ):
                        controller.main(
                            [mode, "--controller-manifest", str(missing)]
                        )

    def test_output_directories_must_be_distinct_and_nonnested(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "overlap"):
                controller._assert_distinct_outputs({"v": root, "t": root})
            with self.assertRaisesRegex(ValueError, "nested"):
                controller._assert_distinct_outputs(
                    {"v": root, "t": root / "nested"}
                )

    def test_unreconciled_all_attempt_economics_blocks_grading(self) -> None:
        complete = {
            arm: {
                "accepted_complete": True,
                "all_attempt_complete": True,
            }
            for arm in controller.ARMS
        }
        controller.require_reconciled_answer_economics(
            {
                "all_attempt_token_economics_reconciled": True,
                "token_receipt_completeness_by_arm": complete,
            }
        )
        incomplete = {arm: dict(row) for arm, row in complete.items()}
        incomplete["e"]["all_attempt_complete"] = False
        with self.assertRaisesRegex(ValueError, "not fully reconciled"):
            controller.require_reconciled_answer_economics(
                {
                    "all_attempt_token_economics_reconciled": False,
                    "token_receipt_completeness_by_arm": incomplete,
                }
            )

    def test_panel_economics_reconciliation_requires_core_token_receipts(self) -> None:
        empty = {
            "accepted": {"calls": 0, "tokens": {}, "completeness": {}},
            "all_attempts": {"calls": 0, "tokens": {}, "completeness": {}},
        }
        self.assertTrue(controller._panel_economics_reconciled(empty))
        complete = {
            scope: {
                "calls": 1,
                "tokens": {
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "total_tokens": 14,
                },
                "completeness": {
                    "input_tokens": True,
                    "output_tokens": True,
                    "total_tokens": True,
                },
            }
            for scope in ("accepted", "all_attempts")
        }
        self.assertTrue(controller._panel_economics_reconciled(complete))
        complete["all_attempts"]["completeness"]["total_tokens"] = False
        self.assertFalse(controller._panel_economics_reconciled(complete))

    def test_zero_panel_finalizer_records_panel_not_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            grading = root / "grading"
            grading.mkdir()
            (grading / "manifest.json").write_text("{}\n", encoding="utf-8")
            gold_path = root / "gold.jsonl"
            question_path = root / "questions.jsonl"
            gold_path.write_text("{}\n", encoding="utf-8")
            question_path.write_text("{}\n", encoding="utf-8")
            result_dir = root / "result"
            bundle = SimpleNamespace(
                manifest_sha256="a" * 64,
                question_ids=("q1",),
                arms=(),
                manifest={
                    "outputs": {
                        "grading": str(grading),
                        "panel": str(root / "panel"),
                        "result": str(result_dir),
                    },
                    "dataset": {"manifest_sha256": "b" * 64},
                    "answer_inputs": {"manifest_sha256": "c" * 64},
                    "snapshots": {
                        "dataset_gold_jsonl": {"snapshot_path": str(gold_path)},
                        "dataset_questions_jsonl": {
                            "snapshot_path": str(question_path)
                        },
                    },
                },
            )
            progress = {
                "all_attempt_token_economics_reconciled": True,
                "token_receipt_completeness_by_arm": {
                    arm: {
                        "accepted_complete": True,
                        "all_attempt_complete": True,
                    }
                    for arm in controller.ARMS
                },
            }
            grading_manifest = {"answer_economics": {}}
            assembled = {
                "status": "completed_registered_analysis",
                "promotion_assessment": {"promoted": False},
            }

            def fake_assemble(**kwargs):
                return {**assembled, "input_hashes": kwargs["input_hashes"]}

            with (
                mock.patch.object(controller, "build_completion_coverage", return_value={}),
                mock.patch.object(controller, "a11_progress", return_value=progress),
                mock.patch.object(
                    controller,
                    "_verified_grading_artifacts",
                    return_value=(grading_manifest, {}, []),
                ),
                mock.patch.object(
                    controller,
                    "_verified_panel_verdicts",
                    return_value=(
                        {},
                        {
                            "accepted": {"calls": 0},
                            "all_attempts": {"calls": 0},
                        },
                    ),
                ),
                mock.patch("a11_grading.load_gold_after_completion", return_value={"q1": {}}),
                mock.patch("a11_grading.final_labels", return_value={arm: {"q1": 0} for arm in controller.ARMS}),
                mock.patch("a11_grading.assemble_result", side_effect=fake_assemble),
                mock.patch.object(controller, "_sealed_payloads", return_value={}),
                mock.patch.object(controller, "_mechanism_outcomes", return_value={}),
                mock.patch.object(controller, "_answer_behavior_outcomes", return_value={}),
                mock.patch.object(controller, "_compilation_economics", return_value={}),
            ):
                final = controller.finalize_result(bundle)
            self.assertEqual(final["status"], "completed_registered_analysis")
            written = json.loads((result_dir / "result.json").read_text())
            self.assertEqual(
                written["input_hashes"]["panel_disposition"],
                "panel_not_required_empty_queue",
            )
            self.assertIsNone(
                written["input_hashes"]["panel_verdict_manifest_sha256"]
            )


if __name__ == "__main__":
    unittest.main()
