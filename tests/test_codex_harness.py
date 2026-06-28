import json
import tempfile
import unittest
from pathlib import Path

import codex_harness


class CodexHarnessTests(unittest.TestCase):
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
            )

            result = codex_harness.run_question(command, "ignored", timeout=1, dry_run=False)

        self.assertEqual(result["status"], "timeout")
        self.assertIn("timeout", result["error"])

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
        self.assertEqual(cmd.stdout_path, paths.event_log_path)

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


if __name__ == "__main__":
    unittest.main()
