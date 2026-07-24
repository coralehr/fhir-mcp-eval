from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import a11b_postprocess as postprocess
import experiment_executor
import experiment_executor_service as service
import experiment_witness as witness


class FakePanelDriver:
    def invoke(
        self,
        invocation: experiment_executor.SealedInvocation,
        capture_dir: Path,
    ) -> experiment_executor.DriverTermination:
        capture_dir.mkdir(parents=True, exist_ok=True)
        items = []
        for line in invocation.prompt.decode().splitlines():
            if line.startswith("{"):
                items.append(json.loads(line))
        (capture_dir / "events.jsonl").write_text(
            "".join(
                json.dumps(row, sort_keys=True) + "\n"
                for row in (
                    {"type": "thread.started", "thread_id": "panel-test"},
                    {"type": "turn.started"},
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 10,
                            "cached_input_tokens": 0,
                            "output_tokens": 2,
                            "reasoning_output_tokens": 1,
                            "total_tokens": 12,
                        },
                    },
                )
            )
        )
        (capture_dir / "answer.json").write_text(
            json.dumps(
                {
                    "verdicts": [
                        {"item_id": item["item_id"], "correct": True}
                        for item in items
                    ]
                },
                sort_keys=True,
            )
            + "\n"
        )
        (capture_dir / "stderr.log").write_bytes(b"")
        return experiment_executor.DriverTermination(
            exit_code=0,
            timed_out=False,
            runtime_sha256=invocation.runtime_sha256,
        )


class A11bPostprocessTests(unittest.TestCase):
    def test_successor_controller_accepts_the_compiler_output_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = {
                "schema_version": "a11-controller-v4",
                "experiment_profile": "a11b-successor-development-v1",
                "inputs": {"question_count": 64, "answer_calls": 192},
                "schedule": {
                    "arms": ["t0", "t1", "e1"],
                    "items": [{} for _index in range(192)],
                },
                "outputs": {
                    "answer_export": str((root / "answer-export").resolve()),
                    "grading": str((root / "grading").resolve()),
                    "result": str((root / "result").resolve()),
                },
            }
            path = root / "controller.json"
            payload = service.canonical_json_line(controller)
            path.write_bytes(payload)
            path.with_suffix(".sha256").write_text(
                postprocess._sha256(payload) + "\n", encoding="ascii"
            )

            observed, digest = postprocess._load_controller(path)

            self.assertEqual(observed, controller)
            self.assertEqual(digest, postprocess._sha256(payload))

    def test_audit_verifier_rejects_unmanifested_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "efficacy/gold.jsonl"
            artifact.parent.mkdir()
            artifact.write_bytes(b"{}\n")
            manifest = {
                "artifacts": {
                    "efficacy/gold.jsonl": postprocess._receipt(artifact.read_bytes())
                }
            }
            manifest_payload = service.canonical_json_line(manifest)
            (root / "manifest.json").write_bytes(manifest_payload)
            manifest_sha = witness.sha256_bytes(manifest_payload)
            (root / "manifest.sha256").write_text(manifest_sha + "\n")

            postprocess._verify_audit_tree(root, manifest_sha)
            (root / "unregistered.json").write_text("{}\n")
            with self.assertRaisesRegex(postprocess.PostprocessError, "inventory"):
                postprocess._verify_audit_tree(root, manifest_sha)

    def test_token_economics_marks_unknown_provider_usage_as_lower_bound(self) -> None:
        economics = postprocess._answer_economics(
            {
                "all_attempts": [
                    {
                        "arm": "t0",
                        "outcome": "provider_failure",
                        "attempt_number": 1,
                        "token_usage": {
                            "input": None,
                            "cached": None,
                            "output": None,
                            "reasoning": None,
                            "total": None,
                            "complete": False,
                        },
                    },
                    {
                        "arm": "t0",
                        "outcome": "accepted",
                        "attempt_number": 2,
                        "token_usage": {
                            "input": 10,
                            "cached": 0,
                            "output": 2,
                            "reasoning": 1,
                            "total": 12,
                            "complete": True,
                        },
                    },
                ]
            }
        )

        self.assertFalse(economics["all_attempt_token_economics_reconciled"])
        self.assertTrue(economics["all_attempt_token_usage_is_lower_bound"])
        self.assertEqual(economics["unknown_usage_attempts_by_arm"]["t0"], 1)
        self.assertEqual(economics["all_attempt_token_usage_by_arm"]["t0"]["total"], 12)

    def test_visible_references_follow_the_compiler_packet_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots: dict[str, dict[str, str]] = {}
            for snapshot_name, arm in (
                ("packet_v", "t0"),
                ("packet_t", "t1"),
                ("packet_e", "e1"),
            ):
                payload: dict[str, object] = {
                    "schema_version": "a11b-event-compiler-v1",
                    "evidence": {
                        "resources": [
                            {"resourceType": "Observation", "id": f"{arm}-obs"}
                        ],
                        "path_citations": [],
                    },
                }
                if arm == "e1":
                    payload["event_groups"] = [
                        {
                            "root_ref": "Procedure/e1-root",
                            "member_refs": [
                                {"reference": "Specimen/e1-member", "depth": 1}
                            ],
                            "typed_edges": [],
                        }
                    ]
                path = root / f"{snapshot_name}.jsonl"
                path.write_text(
                    json.dumps(
                        {
                            "question_id": "q-000",
                            "model_payload_json": json.dumps(payload),
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                snapshots[snapshot_name] = {"snapshot_path": str(path)}

            visible = postprocess._packet_visible_refs({"snapshots": snapshots})

            self.assertEqual(visible["t0"]["q-000"], {"Observation/t0-obs"})
            self.assertEqual(visible["t1"]["q-000"], {"Observation/t1-obs"})
            self.assertEqual(
                visible["e1"]["q-000"],
                {
                    "Observation/e1-obs",
                    "Procedure/e1-root",
                    "Specimen/e1-member",
                },
            )

    def test_stage_publication_is_atomic_on_mid_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "result"
            real_write = postprocess._write_exclusive
            calls = 0

            def fail_second(path: Path, payload: bytes, *, mode: int = 0o400) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated publication crash")
                real_write(path, payload, mode=mode)

            with mock.patch.object(
                postprocess, "_write_exclusive", side_effect=fail_second
            ):
                with self.assertRaisesRegex(OSError, "publication crash"):
                    postprocess._publish_with_manifest(
                        root,
                        {"artifact.json": b"{}\n"},
                        {"schema_version": "test-v1"},
                    )
            self.assertFalse(root.exists())
            self.assertFalse(list(root.parent.glob(".result.publish-*")))

    def _controller(self, root: Path) -> Path:
        runtime = root / "codex"
        runtime.write_bytes(b"pinned synthetic panel runtime")
        runtime.chmod(0o500)
        outputs = {
            "answer_export": str((root / "answer-export").resolve()),
            "grading": str((root / "grading").resolve()),
            "panel": str((root / "panel").resolve()),
            "result": str((root / "result").resolve()),
        }
        controller = {
            "kind": "a11_interleaved_controller_manifest",
            "schema_version": "a11-controller-v4",
            "experiment_profile": "a11b-causal-isolation-v2",
            "run_id": "a" * 64,
            "execution": {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "high",
                "trusted_executor": {"sandbox": {"path": "/usr/bin/false", "sha256": "b" * 64}},
            },
            "grading": {
                "answer_schema_sha256": "c" * 64,
                "panel": {
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "high",
                    "votes": 3,
                    "batch_size": 20,
                    "timeout_seconds": 600,
                    "codex_bin": str(runtime.resolve()),
                    "codex_version": "codex-cli 0.144.1",
                    "codex_binary_sha256": witness.sha256_bytes(runtime.read_bytes()),
                    "panel_source_sha256": "d" * 64,
                },
            },
            "inputs": {
                "question_count": 384,
                "answer_calls": 1152,
                "public_manifest_sha256": "e" * 64,
                "audit_manifest_sha256": "f" * 64,
            },
            "schedule": {
                "arms": ["t0", "t1", "e1"],
                "items": [
                    {
                        "schedule_index": index,
                        "arm": ("t0", "t1", "e1")[index % 3],
                        "question_id": f"q-{index // 3:03d}",
                        "prompt_sha256": "1" * 64,
                    }
                    for index in range(1152)
                ],
            },
            "snapshots": {},
            "outputs": outputs,
        }
        path = root / "controller.json"
        payload = (json.dumps(controller, indent=2, sort_keys=True) + "\n").encode()
        path.write_bytes(payload)
        (root / "controller.sha256").write_text(witness.sha256_bytes(payload) + "\n")
        return path

    def test_audit_is_not_touched_until_completed_export_proof_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = self._controller(root)
            with mock.patch.object(
                postprocess,
                "_verified_export",
                side_effect=postprocess.PostprocessError("incomplete"),
            ), mock.patch.object(postprocess, "_verify_audit_tree") as audit:
                with self.assertRaisesRegex(postprocess.PostprocessError, "incomplete"):
                    postprocess.prepare_grading(
                        controller_path=controller,
                        audit_root=root / "must-not-open",
                    )
            audit.assert_not_called()

    def test_installed_postprocess_sources_must_match_sealed_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            modules = {
                "a11_grading": Path(postprocess.a11b_grading.__file__).resolve(),
                "a11b_postprocess": Path(postprocess.__file__).resolve(),
                "paired_stats": Path(postprocess.paired_stats.__file__).resolve(),
                "panel_grade": Path(postprocess.panel_grade.__file__).resolve(),
                "run_a11_panel": Path(postprocess.run_a11b_panel.__file__).resolve(),
                "run_lock": Path(postprocess.run_lock.__file__).resolve(),
            }
            snapshots = root / "snapshots"
            snapshots.mkdir()
            receipts = {}
            for logical_name, source in modules.items():
                target = snapshots / source.name
                payload = source.read_bytes()
                target.write_bytes(payload)
                receipts[logical_name] = {
                    "snapshot_path": f"/sealed/snapshots/{source.name}",
                    "sha256": postprocess._sha256(payload),
                    "bytes": len(payload),
                }

            postprocess._verify_installed_postprocess_sources(
                controller={"snapshots": receipts},
                bundle_root=root,
            )
            (snapshots / Path(postprocess.__file__).name).write_bytes(b"changed")
            with self.assertRaisesRegex(
                postprocess.PostprocessError, "a11b_postprocess source changed"
            ):
                postprocess._verify_installed_postprocess_sources(
                    controller={"snapshots": receipts},
                    bundle_root=root,
                )

    def test_panel_calls_run_through_a_separate_signed_witness_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = self._controller(root)
            grading = root / "grading"
            grading.mkdir()
            queue_row = {
                "arm": "e1",
                "question_id": "q-000",
                "question": "What synthetic organism was found?",
                "gold": {"acceptable_any": ["O-ABC", "Synthetic organism ABC"]},
                "answer": "O-ABC",
                "insufficiency_reason": None,
            }
            queue = service.canonical_json_line(queue_row)
            (grading / "panel_queue.jsonl").write_bytes(queue)
            (grading / "manifest.json").write_bytes(
                service.canonical_json_line(
                    {
                        "controller_manifest_sha256": witness.sha256_bytes(
                            controller.read_bytes()
                        ),
                        "all_checks_passed": True,
                    }
                )
            )
            (root / "commitment.key").write_bytes(bytes(range(32)))
            key = root / "witness_ed25519"
            subprocess.run(
                [
                    str(witness.SSH_KEYGEN_PATH),
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-f",
                    str(key),
                ],
                check=True,
            )

            manifest = postprocess.run_witnessed_panel(
                controller_path=controller,
                bundle_root=root,
                driver=FakePanelDriver(),
            )

            self.assertEqual(manifest["panel_items"], 1)
            self.assertEqual(manifest["model_calls_reserved"], 3)
            self.assertEqual(manifest["model_calls_closed"], 3)
            self.assertTrue(manifest["token_usage"]["all_attempts_reconciled"])
            self.assertEqual(manifest["token_usage"]["unknown_usage_attempts"], 0)
            self.assertEqual(json.loads((root / "panel/panel_verdicts.json").read_text()), {"e1|q-000": 1})
            witness_export = json.loads(
                (root / "panel/witnessed_panel_export.json").read_text()
            )
            self.assertEqual(witness_export["schedule_length"], 3)
            self.assertEqual(len(witness_export["signed_receipts"]), 6)

            controller_value = json.loads(controller.read_bytes())
            controller_sha = witness.sha256_bytes(controller.read_bytes())
            postprocess._verified_panel(
                controller_value, controller_sha, bundle_root=root
            )
            tampered_verdicts = service.canonical_json_line({"e1|q-000": 0})
            verdict_path = root / "panel/panel_verdicts.json"
            verdict_path.chmod(0o600)
            verdict_path.write_bytes(tampered_verdicts)
            panel_manifest_path = root / "panel/manifest.json"
            panel_manifest = json.loads(panel_manifest_path.read_bytes())
            panel_manifest["artifacts"]["panel_verdicts.json"] = (
                postprocess._receipt(tampered_verdicts)
            )
            panel_manifest_path.chmod(0o600)
            panel_manifest_path.write_bytes(service.canonical_json_line(panel_manifest))
            with self.assertRaisesRegex(
                postprocess.PostprocessError, "majority changed"
            ):
                postprocess._verified_panel(
                    controller_value, controller_sha, bundle_root=root
                )


if __name__ == "__main__":
    unittest.main()
