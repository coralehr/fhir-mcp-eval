from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from a11_answer_inputs import (
    ARMS,
    REGISTERED_DATASET_MANIFEST_SHA256,
    materialize_answer_inputs,
    verify_answer_inputs,
)
from a11_dataset_builder import build_dataset, inspect_source
from tests.test_a11_dataset_builder import _provenance, _write_archive


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _build_dataset(root: Path) -> tuple[Path, dict]:
    archive = root / "sample.zip"
    _write_archive(archive)
    source, _ = inspect_source(archive)
    provenance = root / "provenance.json"
    provenance.write_text(json.dumps(_provenance(source), sort_keys=True))
    output = root / "sealed"
    return output, build_dataset(archive, provenance, output)


class A11AnswerInputsTests(unittest.TestCase):
    def test_materializer_rederives_exact_blind_efficacy_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_dir, dataset_manifest = _build_dataset(root)
            dataset_sha = hashlib.sha256(
                (dataset_dir / "manifest.json").read_bytes()
            ).hexdigest()
            output = root / "answer-inputs"
            timing_path = root / "compilation_timing.json"

            manifest = materialize_answer_inputs(
                dataset_dir,
                output,
                expected_dataset_manifest_sha256=dataset_sha,
                timing_output=timing_path,
            )
            verified = verify_answer_inputs(
                output,
                expected_dataset_manifest_sha256=dataset_sha,
            )

            self.assertEqual(manifest, verified)
            self.assertEqual(manifest["model_calls"], 0)
            self.assertEqual(manifest["question_count"], 120)
            self.assertEqual(manifest["arms"], list(ARMS))
            timing = json.loads(timing_path.read_text())
            self.assertEqual(timing["schema_version"], "a11-compilation-timing-v1")
            self.assertEqual(timing["efficacy_question_count"], 120)
            self.assertEqual(len(timing["rows"]), 120)
            self.assertTrue(
                all(
                    isinstance(value, int) and value >= 0
                    for row in timing["rows"]
                    for key, value in row.items()
                    if key.endswith("_ns")
                )
            )
            self.assertEqual(
                manifest["dataset"]["profile_sha256"],
                dataset_manifest["profile_sha256"],
            )
            self.assertEqual(
                manifest["v_producer_manifest_sha256"],
                json.loads((dataset_dir / "governed_preflight.json").read_text())[
                    "v_manifest_sha256"
                ],
            )

            with (output / "answer_input.csv").open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(
                list(rows[0]), ["question_id", "question", "assumption"]
            )
            self.assertEqual(len(rows), 120)
            self.assertTrue(all("patient" not in key.lower() for key in rows[0]))

            preflight = {
                row["question_id"]: row
                for row in json.loads(
                    (dataset_dir / "governed_preflight.json").read_text()
                )["rows"]
            }
            packet_rows = {
                arm: _jsonl(output / f"{arm}_packets.jsonl") for arm in ARMS
            }
            for arm in ARMS:
                self.assertEqual(len(packet_rows[arm]), 120)
                self.assertEqual(
                    [row["question_id"] for row in packet_rows[arm]],
                    manifest["question_ids"],
                )
                for row in packet_rows[arm]:
                    payload = row["model_payload_json"].encode()
                    self.assertEqual(
                        hashlib.sha256(payload).hexdigest(),
                        row["model_payload_sha256"],
                    )
                    self.assertEqual(len(payload), row["model_payload_utf8_bytes"])
                    expected_key = f"{arm}_model_payload_sha256"
                    self.assertEqual(
                        row["model_payload_sha256"],
                        preflight[row["question_id"]][expected_key],
                    )

            receipts = _jsonl(output / "governed_receipts.jsonl")
            self.assertEqual(len(receipts), 120)
            self.assertTrue(
                all(
                    row["t_shared_retrieval_source_sha256"]
                    == row["e_shared_retrieval_source_sha256"]
                    == row["shared_retrieval_source_sha256"]
                    for row in receipts
                )
            )

    def test_verifier_rejects_payload_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_dir, _ = _build_dataset(root)
            dataset_sha = hashlib.sha256(
                (dataset_dir / "manifest.json").read_bytes()
            ).hexdigest()
            output = root / "answer-inputs"
            materialize_answer_inputs(
                dataset_dir,
                output,
                expected_dataset_manifest_sha256=dataset_sha,
            )
            with (output / "t_packets.jsonl").open("ab") as handle:
                handle.write(b" ")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                verify_answer_inputs(
                    output,
                    expected_dataset_manifest_sha256=dataset_sha,
                )

    def test_registered_dataset_hash_is_independent(self) -> None:
        self.assertEqual(
            REGISTERED_DATASET_MANIFEST_SHA256,
            "442ca8d204fbd81f06e0abaf2ea5022b375deabb71a93d5ebaeccef98e99fe3c",
        )


if __name__ == "__main__":
    unittest.main()
