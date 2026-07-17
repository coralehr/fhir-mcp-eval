from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import a11b_executor_client as client


class A11bExecutorClientTests(unittest.TestCase):
    def test_run_rejects_status_head_that_diverges_from_execution_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = root / "controller.json"
            manifest = {
                "experiment_profile": "a11b-causal-isolation-v2",
                "run_id": "a" * 64,
                "schedule": {"items": [{} for _ in range(1152)]},
            }
            payload = (json.dumps(manifest, sort_keys=True) + "\n").encode()
            controller.write_bytes(payload)
            controller.with_suffix(".sha256").write_text(
                __import__("hashlib").sha256(payload).hexdigest() + "\n"
            )
            statuses = [
                {"ok": True, "result": {"schedule_position": 0, "witness_head": "b" * 64}},
                {
                    "ok": True,
                    "result": {
                        "schedule_position": 1,
                        "witness_head": "d" * 64,
                        "state": "running",
                    },
                },
            ]
            execution = {
                "kind": "executor_call_result",
                "run_id": "a" * 64,
                "request_head": "b" * 64,
                "witness_head": "c" * 64,
                "closed_receipt": {"body": {"attempt_number": 1}},
                "outcome": "accepted",
            }
            with mock.patch.object(client, "_call_service", side_effect=statuses), mock.patch.object(
                client, "_execute_with_intent", return_value=execution
            ):
                with self.assertRaisesRegex(RuntimeError, "witness head diverged"):
                    client.run(
                        controller=controller,
                        key=root / "key",
                        output=root / "receipts",
                    )

    def test_execute_intent_survives_lost_ack_and_replays_same_head(self) -> None:
        response = {
            "ok": True,
            "schema_version": "experiment-executor-service-v1",
            "result": {
                "kind": "executor_call_result",
                "run_id": "a" * 64,
                "request_head": "b" * 64,
                "witness_head": "c" * 64,
                "outcome": "accepted",
                "token_usage": {
                    "input": 1,
                    "cached": 0,
                    "output": 1,
                    "reasoning": 0,
                    "total": 2,
                    "complete": True,
                    "source": "turn.completed",
                },
                "artifact_root_commitment": "d" * 64,
                "opened_receipt": {"body": {"attempt_number": 1}},
                "closed_receipt": {"body": {"attempt_number": 1}},
                "reason": "accepted_complete_capture",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            real_write = client._write_exclusive
            failed = False

            def lose_first_result(path: Path, value: object) -> None:
                nonlocal failed
                if path.name.endswith(".result.json") and not failed:
                    failed = True
                    raise OSError("simulated lost acknowledgement")
                real_write(path, value)

            with mock.patch.object(client, "_call_service", return_value=response) as call:
                with mock.patch.object(
                    client,
                    "_write_exclusive",
                    side_effect=lose_first_result,
                ):
                    with self.assertRaisesRegex(OSError, "lost acknowledgement"):
                        client._execute_with_intent(
                            key=output / "key",
                            output=output,
                            run_id="a" * 64,
                            position=0,
                            expected_head="b" * 64,
                        )
                intent = next(output.glob("*.intent.json"))
                self.assertFalse(list(output.glob("*.result.json")))

                observed = client._execute_with_intent(
                    key=output / "key",
                    output=output,
                    run_id="a" * 64,
                    position=0,
                    expected_head="b" * 64,
                )

            self.assertEqual(observed, response["result"])
            self.assertEqual(call.call_count, 2)
            for invocation in call.call_args_list:
                request = json.loads(invocation.kwargs["request"])
                self.assertEqual(request["expected_head"], "b" * 64)
            self.assertTrue(intent.is_file())
            self.assertEqual(len(list(output.glob("*.result.json"))), 1)


if __name__ == "__main__":
    unittest.main()
