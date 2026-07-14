import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import codex_harness


class CodexHarnessTests(unittest.TestCase):
    def test_public_packet_renderer_is_exact_prompt_payload(self):
        packet = {
            "kind": "bounded_fhir_packet",
            "resources": [{"resourceType": "Observation", "id": "o1"}],
            "sha256": "hidden",
        }
        rendered = codex_harness.render_model_visible_packet(packet)
        prompt = codex_harness.build_prompt(
            {
                "question_id": "q1",
                "question": "What was measured?",
                "packet": packet,
            },
            mode="packet",
        )

        self.assertIn(f"Frozen clinical packet:\n{rendered}\n", prompt)
        self.assertNotIn("hidden", rendered)

    def test_packet_prompt_excludes_gold_and_includes_manifest_fields(self):
        packet = {
            "question_id": "q1",
            "question": "What was the first heart rate?",
            "patient_fhir_id": "Patient/abc",
            "packet": {"resources": [{"resourceType": "Observation", "id": "o1"}]},
            "true_answer": "do-not-leak",
            "proc_query": "SELECT leaked_proc_query",
        }

        prompt = codex_harness.build_prompt(packet, mode="packet", skill_text="Keep first and last values.")

        self.assertIn("What was the first heart rate?", prompt)
        self.assertIn("Keep first and last values.", prompt)
        self.assertIn('"resourceType": "Observation"', prompt)
        self.assertNotIn("do-not-leak", prompt)
        self.assertNotIn("SELECT leaked_proc_query", prompt)

    def test_packet_prompt_hides_query_plan_and_rejects_nested_gold(self):
        record = {
            "question_id": "q1",
            "question": "What was measured?",
            "patient_fhir_id": "Patient/p1",
            "packet": {
                "resources": [{"resourceType": "Observation", "id": "o1"}],
                "source_queries": [
                    {
                        "path": "Observation?code:text=culture",
                        "reason": "fixed microbiology display vocabulary (micro-v1)",
                    }
                ],
                "bounds": {"char_count": 99},
            },
        }

        prompt = codex_harness.build_prompt(record, mode="packet")

        self.assertNotIn("source_queries", prompt)
        self.assertNotIn("micro-v1", prompt)
        self.assertNotIn("char_count", prompt)
        record["packet"]["audit"] = {"true_fhir_ids": {"Observation": ["o1"]}}
        with self.assertRaisesRegex(ValueError, "forbidden benchmark key"):
            codex_harness.build_prompt(record, mode="packet")

    def test_noop_packet_metadata_and_sha_do_not_change_model_prompt(self):
        clinical_packet = {
            "kind": "bounded_fhir_packet",
            "planner": "qo-v2",
            "resources": [{"resourceType": "Observation", "id": "o1"}],
            "aggregate_summary": None,
        }
        baseline = {
            "question_id": "q1",
            "question": "What was measured?",
            "patient_fhir_id": "Patient/p1",
            "packet": {
                **clinical_packet,
                "features": [],
                "pinned_reference_targets": 0,
                "sha256": "baseline-sha",
            },
        }
        noop_treatment = {
            **baseline,
            "packet": {
                **clinical_packet,
                "features": ["include-pinning"],
                "pinned_reference_targets": 0,
                "sha256": "treatment-sha",
            },
        }

        baseline_prompt = codex_harness.build_prompt(baseline, mode="packet")
        treatment_prompt = codex_harness.build_prompt(noop_treatment, mode="packet")

        self.assertEqual(baseline_prompt, treatment_prompt)
        self.assertNotIn("sha256", baseline_prompt)
        self.assertNotIn("include-pinning", treatment_prompt)

    def test_empty_traversal_receipt_is_kept_in_artifact_but_hidden_from_prompt(self):
        clinical_packet = {
            "kind": "bounded_fhir_packet",
            "planner": "qo-v2",
            "resources": [{"resourceType": "Observation", "id": "o1"}],
            "aggregate_summary": None,
        }
        baseline = {
            "question_id": "q1",
            "question": "What organism was found?",
            "patient_fhir_id": "Patient/p1",
            "packet": {**clinical_packet, "features": ["micro-vocab"]},
        }
        treatment = {
            **baseline,
            "packet": {
                **clinical_packet,
                "features": ["micro-traversal", "micro-vocab"],
                "reference_traversal": {
                    "kind": "bounded_exact_reference_traversal",
                    "version": "micro-traversal-v1",
                    "limits": {"max_depth": 2},
                    "stats": {
                        "fetch_attempt_count": 0,
                        "added_resource_count": 0,
                        "path_receipt_count": 0,
                    },
                    "path_receipts": [],
                },
            },
        }

        self.assertIn("reference_traversal", treatment["packet"])
        self.assertEqual(
            codex_harness.build_prompt(baseline, mode="packet"),
            codex_harness.build_prompt(treatment, mode="packet"),
        )

    def test_packet_mode_uses_empty_temporary_working_directory(self):
        requested = Path(codex_harness.__file__).resolve().parent

        with codex_harness.question_working_directory(mode="packet", requested_cwd=requested) as isolated:
            self.assertTrue(isolated.is_absolute())
            self.assertNotEqual(isolated, requested)
            self.assertNotIn(requested, isolated.parents)
            self.assertEqual(list(isolated.iterdir()), [])
            isolated_path = isolated

        self.assertFalse(isolated_path.exists())

    def test_packet_tool_event_quarantines_answer_with_durable_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            qdir = Path(tmp)
            event_log = qdir / "events.jsonl"
            answer_path = qdir / "answer.json"
            answer_path.write_text('{"answer":"leaked"}\n', encoding="utf-8")
            event_log.write_text(
                json.dumps({"type": "item.completed", "item": {"id": "r1", "type": "reasoning"}})
                + "\n"
                + json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"id": "c1", "type": "command_execution", "command": "redacted"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            receipt = codex_harness.enforce_packet_event_integrity(
                event_log_path=event_log,
                answer_path=answer_path,
            )

            contamination_path = qdir / "contamination.json"
            quarantined_answer = qdir / "answer.contaminated.json"
            self.assertTrue(receipt["contaminated"])
            self.assertEqual(len(receipt["findings"]), 1)
            self.assertEqual(receipt["findings"][0]["item_type"], "command_execution")
            self.assertFalse(answer_path.exists())
            self.assertTrue(quarantined_answer.exists())
            self.assertTrue(contamination_path.exists())
            self.assertTrue(json.loads(contamination_path.read_text())["contaminated"])
            self.assertEqual(codex_harness.terminal_question_status(qdir), "contaminated")

    def test_malformed_packet_event_log_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            qdir = Path(tmp)
            event_log = qdir / "events.jsonl"
            answer_path = qdir / "answer.json"
            answer_path.write_text('{"answer":"unverifiable"}\n', encoding="utf-8")
            event_log.write_text(
                json.dumps({"type": "item.completed", "item": {"type": "reasoning"}})
                + "\n"
                + '{"type":"item.completed","item":',
                encoding="utf-8",
            )

            receipt = codex_harness.enforce_packet_event_integrity(
                event_log_path=event_log,
                answer_path=answer_path,
            )

            self.assertTrue(receipt["contaminated"])
            self.assertEqual(receipt["parse_error_lines"], [2])
            self.assertFalse(answer_path.exists())
            self.assertTrue((qdir / "contamination.json").exists())

    def test_missing_empty_and_incomplete_packet_event_logs_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = {
                "missing": None,
                "empty": "",
                "scalar": json.dumps("not-an-event") + "\n",
                "incomplete": json.dumps(
                    {"type": "item.completed", "item": {"type": "reasoning"}}
                )
                + "\n",
            }
            for name, content in cases.items():
                with self.subTest(name=name):
                    qdir = root / name
                    qdir.mkdir()
                    answer_path = qdir / "answer.json"
                    event_log = qdir / "events.jsonl"
                    answer_path.write_text('{"answer":"unverifiable"}\n', encoding="utf-8")
                    if content is not None:
                        event_log.write_text(content, encoding="utf-8")

                    receipt = codex_harness.enforce_packet_event_integrity(
                        event_log_path=event_log,
                        answer_path=answer_path,
                    )

                    self.assertTrue(receipt["contaminated"])
                    self.assertTrue(receipt["integrity_errors"])
                    self.assertFalse(answer_path.exists())
                    self.assertTrue((qdir / "contamination.json").exists())

    def test_completed_tool_free_packet_event_log_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            event_log = Path(tmp) / "events.jsonl"
            event_log.write_text(
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}})
                + "\n",
                encoding="utf-8",
            )

            receipt = codex_harness.audit_event_log(event_log)

        self.assertFalse(receipt["contaminated"])
        self.assertEqual(receipt["turn_completed_count"], 1)
        self.assertEqual(receipt["integrity_errors"], [])
        self.assertFalse(
            codex_harness.is_retryable_incomplete_packet_audit(receipt)
        )

    def test_only_explicit_tool_free_provider_failure_is_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            provider_failure = root / "provider-failure.jsonl"
            provider_failure.write_text(
                "\n".join(
                    json.dumps(event)
                    for event in (
                        {"type": "thread.started", "thread_id": "thread-test"},
                        {"type": "turn.started"},
                        {"type": "error", "message": "usage limit"},
                        {
                            "type": "turn.failed",
                            "error": {"message": "usage limit"},
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            retryable = codex_harness.audit_event_log(provider_failure)
            self.assertTrue(
                codex_harness.is_retryable_incomplete_packet_audit(retryable)
            )
            self.assertTrue(
                codex_harness.retryable_incomplete_packet_marker_matches(
                    {**retryable, "quarantine_path": None},
                    retryable,
                )
            )

            tool_failure = root / "tool-failure.jsonl"
            tool_failure.write_text(
                provider_failure.read_text(encoding="utf-8")
                + json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "command_execution"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            contaminated = codex_harness.audit_event_log(tool_failure)
            self.assertFalse(
                codex_harness.is_retryable_incomplete_packet_audit(contaminated)
            )

            no_newline = root / "no-newline.jsonl"
            no_newline.write_bytes(provider_failure.read_bytes()[:-1])
            invalid_utf8 = root / "invalid-utf8.jsonl"
            invalid_utf8.write_bytes(provider_failure.read_bytes() + b"\xff")
            hidden_tool_key = root / "hidden-tool-key.jsonl"
            hidden_events = [
                {"type": "thread.started", "thread_id": "thread-test"},
                {"type": "turn.started"},
                {
                    "type": "error",
                    "message": "usage limit",
                    "tool_call": {"name": "shell"},
                },
                {
                    "type": "turn.failed",
                    "error": {"message": "usage limit"},
                },
            ]
            hidden_tool_key.write_text(
                "\n".join(json.dumps(event) for event in hidden_events) + "\n",
                encoding="utf-8",
            )
            mismatched_error = root / "mismatched-error.jsonl"
            mismatched_events = [
                {"type": "thread.started", "thread_id": "thread-test"},
                {"type": "turn.started"},
                {"type": "error", "message": "usage limit"},
                {"type": "turn.failed", "error": {"message": "other"}},
            ]
            mismatched_error.write_text(
                "\n".join(json.dumps(event) for event in mismatched_events) + "\n",
                encoding="utf-8",
            )
            duplicate_type = root / "duplicate-type.jsonl"
            duplicate_type.write_text(
                '{"type":"thread.started","thread_id":"thread-test"}\n'
                '{"type":"turn.started"}\n'
                '{"type":"item.completed","type":"error",'
                '"message":"usage limit"}\n'
                '{"type":"turn.failed","error":{"message":"usage limit"}}\n',
                encoding="utf-8",
            )
            for path in (
                no_newline,
                invalid_utf8,
                hidden_tool_key,
                mismatched_error,
                duplicate_type,
            ):
                with self.subTest(path=path.name):
                    audit = codex_harness.audit_event_log(path)
                    self.assertFalse(
                        codex_harness.is_retryable_incomplete_packet_audit(audit)
                    )

    def test_packet_mode_requires_packet_json_coverage(self):
        rows = [{"question_id": "q1"}, {"question_id": "q2"}]

        with self.assertRaises(SystemExit):
            codex_harness.validate_packet_coverage(mode="packet", rows=rows, packets={"q1": {}}, packet_json=Path("packets.jsonl"))

        codex_harness.validate_packet_coverage(mode="mcp", rows=rows, packets={}, packet_json=None)

    def test_repo_out_dir_must_be_under_runs_unless_explicitly_allowed(self):
        repo = Path(codex_harness.__file__).resolve().parent

        codex_harness.validate_out_dir(repo / "runs" / "ok", allow_public_artifact=False)
        codex_harness.validate_out_dir(Path("/tmp/codex-ok"), allow_public_artifact=False)
        with self.assertRaises(SystemExit):
            codex_harness.validate_out_dir(repo / "public-output", allow_public_artifact=False)
        codex_harness.validate_out_dir(repo / "public-output", allow_public_artifact=True)

    def test_timeout_is_recorded_as_question_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "sleep.py"
            script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
            command = codex_harness.CodexCommand(
                args=["python3", str(script)],
                stdout_path=root / "events.jsonl",
                stderr_path=root / "stderr.log",
            )

            result = codex_harness.run_question(command, "ignored", timeout=1, dry_run=False)

        self.assertEqual(result["status"], "timeout")
        self.assertIn("timeout", result["error"])

    def test_run_question_separates_and_rejects_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "emit.py"
            script.write_text(
                "import sys\n"
                "print('{\"type\":\"thread.started\",\"thread_id\":\"t1\"}')\n"
                "print('provider diagnostic', file=sys.stderr)\n",
                encoding="utf-8",
            )
            command = codex_harness.CodexCommand(
                args=["python3", str(script)],
                stdout_path=root / "events.jsonl",
                stderr_path=root / "stderr.log",
            )

            result = codex_harness.run_question(
                command, "ignored", timeout=5, dry_run=False
            )

            self.assertEqual(result["status"], "error")
            self.assertEqual(
                command.stdout_path.read_text(encoding="utf-8"),
                '{"type":"thread.started","thread_id":"t1"}\n',
            )
            self.assertEqual(
                command.stderr_path.read_text(encoding="utf-8"),
                "provider diagnostic\n",
            )
            self.assertEqual(
                result["stderr_integrity"],
                {
                    "exists": True,
                    "empty": False,
                    "byte_count": 20,
                    "sha256": codex_harness.sha256_file(command.stderr_path),
                    "utf8_valid": True,
                    "terminal_newline": True,
                },
            )

    def test_run_question_accepts_empty_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "emit.py"
            script.write_text("print('{}')\n", encoding="utf-8")
            command = codex_harness.CodexCommand(
                args=["python3", str(script)],
                stdout_path=root / "events.jsonl",
                stderr_path=root / "stderr.log",
            )

            result = codex_harness.run_question(
                command, "ignored", timeout=5, dry_run=False
            )

            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["stderr_integrity"]["empty"])

    def test_codex_command_is_noninteractive_and_logs_structured_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            paths = codex_harness.paths_for_question(out_dir, "q/1")
            cmd = codex_harness.build_codex_command(
                prompt="Answer the question.",
                schema_path=Path("schemas/codex_answer.schema.json"),
                output_path=paths.answer_path,
                event_log_path=paths.event_log_path,
                cwd=Path("/tmp/eval"),
                model="gpt-5",
                sandbox="read-only",
            )

        joined = " ".join(cmd.args)
        self.assertIn("codex exec", joined)
        self.assertIn("--json", cmd.args)
        self.assertIn("--output-schema", cmd.args)
        self.assertIn("--output-last-message", cmd.args)
        self.assertIn("--ignore-user-config", cmd.args)
        self.assertIn("--ignore-rules", cmd.args)
        self.assertEqual(cmd.stdout_path, paths.event_log_path.resolve())
        self.assertEqual(cmd.stderr_path, paths.stderr_path.resolve())
        schema_arg = Path(cmd.args[cmd.args.index("--output-schema") + 1])
        output_arg = Path(cmd.args[cmd.args.index("--output-last-message") + 1])
        self.assertTrue(schema_arg.is_absolute())
        self.assertTrue(output_arg.is_absolute())

    def test_manifest_records_hashes_and_codex_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_file = root / "input.csv"
            input_file.write_text("question_id,question\nq1,hi\n", encoding="utf-8")
            schema = root / "schema.json"
            schema.write_text('{"type":"object"}\n', encoding="utf-8")
            manifest_path = root / "manifest.json"

            manifest = codex_harness.write_manifest(
                manifest_path=manifest_path,
                run_config={"mode": "packet", "substrate": "codex_subscription"},
                files={"input": input_file, "schema": schema},
                codex_version="codex-cli 0.142.1",
                git_commit="abc123",
                git_dirty=True,
            )

            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["codex_version"], "codex-cli 0.142.1")
            self.assertEqual(loaded["git"]["commit"], "abc123")
            self.assertTrue(loaded["git"]["dirty"])
            self.assertEqual(loaded["files"]["input"]["sha256"], codex_harness.sha256_file(input_file))
            self.assertEqual(loaded, manifest)

            resumed = codex_harness.write_manifest(
                manifest_path=manifest_path,
                run_config={"mode": "packet", "substrate": "codex_subscription"},
                files={"input": input_file, "schema": schema},
                codex_version="codex-cli 0.142.1",
                git_commit="abc123",
                git_dirty=True,
            )
            self.assertEqual(resumed, manifest)

            with self.assertRaisesRegex(ValueError, "immutable harness manifest"):
                codex_harness.write_manifest(
                    manifest_path=manifest_path,
                    run_config={"mode": "packet", "substrate": "changed"},
                    files={"input": input_file, "schema": schema},
                    codex_version="codex-cli 0.142.1",
                    git_commit="abc123",
                    git_dirty=True,
                )

    def test_live_success_requires_clean_ok_answer_not_file_existence(self):
        with tempfile.TemporaryDirectory() as tmp:
            answer = Path(tmp) / "answer.json"
            answer.write_text("{}", encoding="utf-8")
            base = {
                "answer_path": str(answer),
                "event_integrity": {"contaminated": False},
            }

            self.assertTrue(
                codex_harness.question_result_succeeded(
                    {**base, "status": "ok", "returncode": 0}, dry_run=False
                )
            )
            for status, returncode in (
                ("error", 1),
                ("timeout", None),
                ("contaminated", 0),
            ):
                with self.subTest(status=status):
                    self.assertFalse(
                        codex_harness.question_result_succeeded(
                            {**base, "status": status, "returncode": returncode},
                            dry_run=False,
                        )
                    )
            self.assertFalse(
                codex_harness.question_result_succeeded(
                    {
                        **base,
                        "status": "ok",
                        "returncode": 0,
                        "event_integrity": {"contaminated": True},
                    },
                    dry_run=False,
                )
            )
            self.assertTrue(
                codex_harness.question_result_succeeded(
                    {
                        "answer_path": str(answer),
                        "status": "ok",
                        "returncode": 0,
                    },
                    dry_run=False,
                    mode="mcp",
                )
            )

    def test_skip_existing_stale_prompt_is_quarantined_and_never_recertified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.csv"
            input_path.write_text(
                "question_id,question,patient_fhir_id\nq1,What was measured?,Patient/p1\n",
                encoding="utf-8",
            )
            packet_path = root / "packets.jsonl"
            packet_record = {
                "question_id": "q1",
                "packet": {
                    "resources": [{"resourceType": "Observation", "id": "o1"}]
                },
            }
            packet_path.write_text(json.dumps(packet_record) + "\n", encoding="utf-8")
            schema_path = root / "schema.json"
            schema_path.write_text(
                (Path(codex_harness.__file__).parent / "schemas/codex_answer.schema.json").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            out_dir = root / "run"
            paths = codex_harness.paths_for_question(out_dir, "q1")
            paths.prompt_path.write_text("stale prompt\n", encoding="utf-8")
            paths.answer_path.write_text(
                json.dumps(
                    {
                        "answer": "value",
                        "source_resource_ids": ["Observation/o1"],
                        "evidence_summary": "evidence",
                        "insufficiency_reason": None,
                    }
                ),
                encoding="utf-8",
            )
            paths.event_log_path.write_text(
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3}})
                + "\n",
                encoding="utf-8",
            )
            argv = [
                "codex_harness.py",
                "--mode",
                "packet",
                "--input",
                str(input_path),
                "--packet-json",
                str(packet_path),
                "--schema",
                str(schema_path),
                "--out-dir",
                str(out_dir),
                "--question-id",
                "q1",
                "--live",
                "--skip-existing",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(codex_harness, "run_version", return_value="codex test"),
                mock.patch.object(
                    codex_harness,
                    "git_commit_and_dirty",
                    return_value=("commit", False),
                ),
                mock.patch.object(codex_harness, "run_question") as run_question,
            ):
                result = codex_harness.main()

            self.assertEqual(result, 1)
            run_question.assert_not_called()
            self.assertFalse(paths.answer_path.exists())
            self.assertTrue(paths.answer_path.with_name("answer.stale.json").exists())
            marker = json.loads(
                paths.answer_path.with_name("stale_artifact.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(marker["reason"], "prompt_missing_or_mismatch")


if __name__ == "__main__":
    unittest.main()
