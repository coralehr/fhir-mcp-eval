from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from a11_evidence_core import canonical_bytes
from a11b_corpus_builder import (
    ARM_ARTIFACTS,
    _assert_public_blind,
    _event_fields,
    build_case,
    construct_corpus,
    derive_corpus_key,
    materialize_corpus,
)
from a11b_event_compiler import ARM_E1, ARM_T0, ARM_T1


ROOT = Path(__file__).resolve().parents[1]
POWER_RECEIPT = json.loads(
    (ROOT / "docs" / "results" / "a11b-power-receipt.json").read_text()
)
NONCE_KEY = bytes.fromhex("11" * 32)


def _bundle(index: int, *, noise: int = 4) -> dict[str, object]:
    patient_id = f"patient-{index:03d}"
    entries = [
        {
            "resource": {
                "resourceType": "Patient",
                "id": patient_id,
                "meta": {"versionId": "1"},
                "gender": "female",
                "birthDate": "1980-01-01",
            }
        }
    ]
    for item in range(noise):
        entries.append(
            {
                "resource": {
                    "resourceType": "Condition",
                    "id": f"condition-{index:03d}-{item:02d}",
                    "meta": {"versionId": "1"},
                    "subject": {"reference": f"Patient/{patient_id}"},
                    "clinicalStatus": {
                        "coding": [{"code": "active", "display": "Active"}]
                    },
                    "code": {"text": f"Synthetic condition {item}"},
                }
            }
        )
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


class A11bCorpusBuilderTests(unittest.TestCase):
    def test_public_blind_scan_rejects_all_selected_path_gold_fields(self) -> None:
        for field in (
            "selected_terminal_resource_ref",
            "selected_path_refs",
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                _assert_public_blind(
                    {"packet.json": f'{{"{field}":["Observation/LEAK"]}}'.encode()}
                )

    def test_corpus_key_is_publicly_derived_without_operator_randomness(self) -> None:
        first = derive_corpus_key(
            generation_receipt_sha256="1" * 64,
            power_receipt_sha256="2" * 64,
        )
        same = derive_corpus_key(
            generation_receipt_sha256="1" * 64,
            power_receipt_sha256="2" * 64,
        )
        changed = derive_corpus_key(
            generation_receipt_sha256="1" * 64,
            power_receipt_sha256="3" * 64,
        )
        self.assertEqual(first, same)
        self.assertNotEqual(first, changed)
        self.assertEqual(len(first), 32)

    def test_precision_ambiguous_intervals_overlap_at_selected_extreme(self) -> None:
        first = _event_fields("precision_ambiguous", "first")
        latest = _event_fields("precision_ambiguous", "latest")

        self.assertEqual(first[0], {"effectiveDateTime": "2100-01-01"})
        self.assertEqual(
            first[1], {"effectiveDateTime": "2100-01-01T12:00:00Z"}
        )
        self.assertEqual(latest[2], {"effectiveDateTime": "2100-01-03"})
        self.assertEqual(
            latest[1], {"effectiveDateTime": "2100-01-03T12:00:00Z"}
        )

    def test_one_case_has_three_roots_and_only_registered_arm_differences(self) -> None:
        case = build_case(
            _bundle(0),
            split="efficacy",
            family_index=0,
            occurrence=0,
            nonce_key=NONCE_KEY,
        )

        self.assertEqual(len(case["source"]["root_refs"]), 3)
        self.assertEqual(set(case["compiled"]["arms"]), {ARM_T0, ARM_T1, ARM_E1})
        receipt = case["compiled"]["equivalence_receipt"]
        self.assertEqual(len(set(receipt["arm_evidence_sha256"].values())), 1)
        self.assertEqual(len(set(receipt["arm_path_citations_sha256"].values())), 1)
        self.assertNotIn("gold", json.dumps(case["compiled"]["arms"], sort_keys=True))
        self.assertNotIn("answerable", json.dumps(case["compiled"]["arms"], sort_keys=True))
        self.assertIn(
            case["gold"]["selected_terminal_resource_ref"],
            case["gold"]["selected_path_refs"],
        )
        public = json.dumps(case["compiled"]["arms"], sort_keys=True)
        for forbidden in (
            "patient-000",
            "condition-000-00",
            '"gender"',
            '"birthDate"',
        ):
            self.assertNotIn(forbidden, public)

    def test_full_corpus_is_patient_disjoint_balanced_and_blind(self) -> None:
        bundles = [_bundle(index, noise=0) for index in range(448)]
        corpus = construct_corpus(
            bundles,
            power_receipt=POWER_RECEIPT,
            nonce_key=NONCE_KEY,
        )

        self.assertEqual(len(corpus["development"]["questions"]), 64)
        self.assertEqual(len(corpus["efficacy"]["questions"]), 384)
        self.assertFalse(
            set(corpus["development"]["patient_ids"]).intersection(
                corpus["efficacy"]["patient_ids"]
            )
        )
        cells = Counter(
            (row["family"], row["depth"])
            for row in corpus["efficacy"]["audit"]
        )
        self.assertEqual(set(cells.values()), {48})
        temporal = Counter(row["temporal_policy"] for row in corpus["efficacy"]["audit"])
        self.assertEqual(temporal, {"first": 192, "latest": 192})
        answerability = Counter(row["answerable"] for row in corpus["efficacy"]["gold"])
        self.assertEqual(answerability, {True: 288, False: 96})
        self.assertTrue(
            all(
                (len(row["selected_path_refs"]) >= 3)
                if row["answerable"]
                else row["selected_path_refs"] == []
                for row in corpus["efficacy"]["gold"]
            )
        )
        clusters = [
            row["patient_cluster_sha256"] for row in corpus["efficacy"]["gold"]
        ]
        self.assertEqual(len(set(clusters)), 384)
        self.assertTrue(all(len(value) == 64 for value in clusters))
        self.assertNotIn(
            hashlib.sha256(b"patient-000").hexdigest(),
            clusters,
        )
        reasons = Counter(
            row["failure_mode"]
            for row in corpus["efficacy"]["gold"]
            if not row["answerable"]
        )
        self.assertEqual(set(reasons.values()), {16})
        self.assertEqual(set(corpus["efficacy"]["packets"]), set(ARM_ARTIFACTS))
        public = canonical_bytes(
            {
                "questions": corpus["efficacy"]["questions"],
                "packets": corpus["efficacy"]["packets"],
            }
        ).decode()
        for forbidden in (
            '"gold"',
            '"answerable"',
            '"failure_mode"',
            '"reference_answer"',
            '"selected_root_ref"',
            '"selected_path_refs"',
            '"selected_terminal_resource_ref"',
        ):
            self.assertNotIn(forbidden, public)

    def test_nonce_key_is_required_for_question_identity(self) -> None:
        first = build_case(
            _bundle(1),
            split="development",
            family_index=3,
            occurrence=5,
            nonce_key=NONCE_KEY,
        )
        same = build_case(
            _bundle(1),
            split="development",
            family_index=3,
            occurrence=5,
            nonce_key=NONCE_KEY,
        )
        different = build_case(
            _bundle(1),
            split="development",
            family_index=3,
            occurrence=5,
            nonce_key=bytes.fromhex("22" * 32),
        )

        self.assertEqual(first["question"], same["question"])
        self.assertNotEqual(
            first["question"]["question_id"],
            different["question"]["question_id"],
        )

    def test_materialization_is_reproducible_and_physically_separates_gold(self) -> None:
        corpus = construct_corpus(
            [_bundle(index, noise=0) for index in range(448)],
            power_receipt=POWER_RECEIPT,
            nonce_key=NONCE_KEY,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {
                "public_a": root / "public-a",
                "audit_a": root / "audit-a",
                "public_b": root / "public-b",
                "audit_b": root / "audit-b",
            }
            for suffix in ("a", "b"):
                materialize_corpus(
                    corpus,
                    public_root=paths[f"public_{suffix}"],
                    audit_root=paths[f"audit_{suffix}"],
                    generation_spec_sha256="1" * 64,
                    generation_receipt_sha256="2" * 64,
                    power_receipt_sha256="3" * 64,
                )

            def tree_bytes(path: Path) -> dict[str, bytes]:
                return {
                    item.relative_to(path).as_posix(): item.read_bytes()
                    for item in sorted(path.rglob("*"))
                    if item.is_file()
                }

            self.assertEqual(tree_bytes(paths["public_a"]), tree_bytes(paths["public_b"]))
            self.assertEqual(tree_bytes(paths["audit_a"]), tree_bytes(paths["audit_b"]))
            public_names = set(tree_bytes(paths["public_a"]))
            self.assertFalse(any("gold" in name or "audit" in name for name in public_names))
            self.assertTrue((paths["audit_a"] / "efficacy" / "gold.jsonl").is_file())
            public_payload = b"".join(tree_bytes(paths["public_a"]).values()).lower()
            self.assertNotIn(b'"reference_answer"', public_payload)
            self.assertNotIn(b'"failure_mode"', public_payload)
            public_manifest = json.loads(
                (paths["public_a"] / "manifest.json").read_text()
            )
            audit_manifest = json.loads(
                (paths["audit_a"] / "manifest.json").read_text()
            )
            self.assertFalse(public_manifest["contains_raw_patient_identifiers"])
            self.assertTrue(public_manifest["contains_synthetic_fhir_ids"])
            self.assertTrue(audit_manifest["patient_clusters_are_keyed"])
            self.assertIn("corpus_derivation_key_sha256", public_manifest)
            self.assertNotIn("nonce_key_id", public_manifest)


if __name__ == "__main__":
    unittest.main()
