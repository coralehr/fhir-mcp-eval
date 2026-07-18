from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import a11b_postprocess
import a11b_successor_development_postprocess as postprocess


def _usage() -> dict[str, object]:
    return {
        "input": 10,
        "cached": 2,
        "output": 4,
        "reasoning": 1,
        "total": 14,
        "complete": True,
        "source": "turn.completed",
    }


def _attempt(index: int, answer: str) -> dict[str, object]:
    answer_bytes = json.dumps(
        {
            "status": "answered",
            "answer": answer,
            "source_resource_ids": ["Observation/example"],
            "evidence_summary": "Visible evidence supports the result.",
            "insufficiency_reason": None,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    artifact = {
        "kind": "codex_capture",
        "capture": {
            "files_base64": {
                "answer.json": base64.b64encode(answer_bytes).decode(),
                "events.jsonl": base64.b64encode(b"event\n").decode(),
                "stderr.log": "",
            }
        },
    }
    raw = a11b_postprocess._canonical(artifact)
    receipt = a11b_postprocess._receipt(raw)
    return {
        "descriptor": {"schedule_index": index, "attempt_number": 1},
        "outcome": "accepted",
        "token_usage": _usage(),
        "artifact_base64": base64.b64encode(raw).decode(),
        "artifact_sha256": receipt["sha256"],
        "artifact_bytes": receipt["bytes"],
    }


class _Executor:
    def __init__(self, export: dict[str, object]) -> None:
        self.export = export

    def export_completed_run(self) -> dict[str, object]:
        return self.export


class SuccessorDevelopmentPostprocessTests(unittest.TestCase):
    def test_valid_no_headroom_gate_is_published_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = {
                "experiment_profile": postprocess.PROFILE,
                "run_id": "a" * 64,
                "inputs": {
                    "answer_calls": 192,
                    "audit_manifest_sha256": "c" * 64,
                },
                "schedule": {"items": []},
                "outputs": {"result": str(root / "results/final")},
            }
            grading_result = {
                "assignments": [],
                "outcomes": [],
                "manifest": {},
            }
            failed_gate = {
                "status": "failed",
                "development_result_manifest_sha256": "d" * 64,
                "model_calls": 0,
            }
            with mock.patch.object(
                postprocess, "_export_rows", return_value=([], [])
            ), mock.patch.object(
                postprocess.a11b_postprocess,
                "_verify_audit_tree",
                return_value={"artifacts": {}},
            ), mock.patch.object(
                postprocess.a11b_postprocess, "_read_jsonl", return_value=[]
            ), mock.patch.object(
                postprocess.a11b_successor_development_grading,
                "compile_result",
                return_value=grading_result,
            ), mock.patch.object(
                postprocess.a11b_successor_dev_gate,
                "compile_gate_receipt",
                return_value=failed_gate,
            ):
                result = postprocess.run_all(
                    bundle_root=root,
                    audit_root=root / "audit-input",
                    trusted_executor=object(),
                    controller=controller,
                    controller_sha256="b" * 64,
                )

            self.assertEqual(result["promotion"], "development_gate_failed")
            gate = json.loads((root / "results/final/gate.json").read_bytes())
            self.assertEqual(gate["status"], "failed")

    def test_exact_completed_export_runs_zero_model_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audit = root / "audit-input"
            (audit / "development").mkdir(parents=True)
            gold = [
                {
                    "question_id": f"q-{index:02d}",
                    "patient_cluster_sha256": f"{index:064x}",
                    "answerable": True,
                    "reference_answer": {"code": "123", "display": "Alpha"},
                }
                for index in range(64)
            ]
            gold_bytes = b"".join(a11b_postprocess._canonical(row) for row in gold)
            (audit / "development/gold.jsonl").write_bytes(gold_bytes)
            audit_manifest = {
                "schema_version": "test-audit-v1",
                "artifacts": {
                    "development/gold.jsonl": a11b_postprocess._receipt(gold_bytes)
                },
            }
            manifest_bytes = a11b_postprocess._canonical(audit_manifest)
            manifest_sha = a11b_postprocess._sha256(manifest_bytes)
            (audit / "manifest.json").write_bytes(manifest_bytes)
            (audit / "manifest.sha256").write_text(manifest_sha + "\n")

            schedule = []
            attempts = []
            index = 0
            for question in gold:
                for arm in ("t0", "t1", "e1"):
                    schedule.append(
                        {
                            "schedule_index": index,
                            "question_id": question["question_id"],
                            "arm": arm,
                        }
                    )
                    wrong = (
                        (question["question_id"], arm)
                        in {("q-00", "e1"), ("q-01", "t0")}
                    )
                    attempts.append(_attempt(index, "wrong" if wrong else "Alpha"))
                    index += 1
            controller = {
                "experiment_profile": postprocess.PROFILE,
                "run_id": "a" * 64,
                "inputs": {
                    "answer_calls": 192,
                    "audit_manifest_sha256": manifest_sha,
                },
                "schedule": {"items": schedule},
                "outputs": {"result": str(root / "results/final")},
            }
            exported = {
                "run_id": "a" * 64,
                "schedule_length": 192,
                "accepted_slots": 192,
                "attempts": attempts,
            }

            result = postprocess.run_all(
                bundle_root=root,
                audit_root=audit,
                trusted_executor=_Executor(exported),
                controller=controller,
                controller_sha256="b" * 64,
            )

            self.assertEqual(result["promotion"], "development_gate_passed")
            gate = json.loads((root / "results/final/gate.json").read_bytes())
            self.assertEqual(gate["status"], "passed")
            self.assertEqual(gate["model_calls"], 0)


if __name__ == "__main__":
    unittest.main()
