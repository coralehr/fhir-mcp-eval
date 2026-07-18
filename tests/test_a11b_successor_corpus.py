from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import a11b_answer_contract
import a11b_successor_corpus as successor
from tests.test_a11b_corpus_builder import NONCE_KEY, POWER_RECEIPT, _bundle


ROOT = Path(__file__).resolve().parents[1]
SUCCESSOR = ROOT / "docs/results/a11b-successor-artifacts"


class A11bSuccessorCorpusTests(unittest.TestCase):
    def test_registered_successor_generation_identity_is_exact(self) -> None:
        spec_bytes = (SUCCESSOR / "source-c-spec.json").read_bytes()
        receipt_bytes = (SUCCESSOR / "source-c-receipt.json").read_bytes()
        receipt = json.loads(receipt_bytes)
        successor.assert_registered_generation(
            spec_bytes=spec_bytes,
            receipt_bytes=receipt_bytes,
            receipt=receipt,
        )

        substituted = copy.deepcopy(receipt)
        substituted["raw_output"]["content_sha256"] = "5" * 64
        substituted_bytes = json.dumps(substituted, sort_keys=True).encode()
        with self.assertRaisesRegex(ValueError, "registered successor"):
            successor.assert_registered_generation(
                spec_bytes=spec_bytes,
                receipt_bytes=substituted_bytes,
                receipt=substituted,
            )

    def test_spent_generation_cannot_be_reopened(self) -> None:
        spent = {
            "raw_output": {
                "content_sha256": successor.SPENT_RAW_OUTPUT_CONTENT_SHA256
            }
        }
        with self.assertRaisesRegex(ValueError, "spent"):
            successor.assert_fresh_generation(
                spent,
                generation_receipt_sha256="4" * 64,
            )
        with self.assertRaisesRegex(ValueError, "spent"):
            successor.assert_fresh_generation(
                {"raw_output": {"content_sha256": "5" * 64}},
                generation_receipt_sha256=(
                    successor.SPENT_GENERATION_RECEIPT_SHA256
                ),
            )

    def test_constructs_only_the_fresh_development_split_with_v2_prompts(self) -> None:
        corpus = successor.construct_development_corpus(
            [_bundle(index, noise=0) for index in range(448)],
            power_receipt=POWER_RECEIPT,
            nonce_key=NONCE_KEY,
        )

        self.assertEqual(corpus["schema_version"], successor.CORPUS_VERSION)
        self.assertEqual(len(corpus["development"]["questions"]), 64)
        self.assertEqual(corpus["reserved_efficacy_patient_count"], 384)
        self.assertNotIn("efficacy", corpus)
        for records in corpus["development"]["packets"].values():
            self.assertEqual(len(records), 64)
            self.assertTrue(
                all(
                    record["answer_contract_version"]
                    == a11b_answer_contract.CONTRACT_VERSION
                    for record in records
                )
            )

    def test_materialization_cannot_open_or_disclose_the_efficacy_split(self) -> None:
        corpus = successor.construct_development_corpus(
            [_bundle(index, noise=0) for index in range(448)],
            power_receipt=POWER_RECEIPT,
            nonce_key=NONCE_KEY,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public, audit = successor.materialize_development_corpus(
                corpus,
                public_root=root / "public",
                audit_root=root / "audit",
                generation_spec_sha256="1" * 64,
                generation_receipt_sha256="2" * 64,
                power_receipt_sha256="3" * 64,
            )

            self.assertFalse((root / "public" / "efficacy").exists())
            self.assertFalse((root / "audit" / "efficacy").exists())
            self.assertFalse(public["efficacy_materialized"])
            self.assertFalse(audit["efficacy_materialized"])
            self.assertEqual(public["split_counts"], {"development": 64})
            public_bytes = b"".join(
                path.read_bytes()
                for path in sorted((root / "public").rglob("*"))
                if path.is_file()
            ).lower()
            self.assertNotIn(b'"gold"', public_bytes)
            self.assertNotIn(b'"answerable"', public_bytes)

    def test_materialization_rejects_every_misaligned_development_collection(
        self,
    ) -> None:
        original = successor.construct_development_corpus(
            [_bundle(index, noise=0) for index in range(448)],
            power_receipt=POWER_RECEIPT,
            nonce_key=NONCE_KEY,
        )
        mutations = {
            "questions": lambda value: value["development"]["questions"].pop(),
            "t0 packets": lambda value: value["development"]["packets"]["t0"].pop(),
            "t1 packets": lambda value: value["development"]["packets"]["t1"].pop(),
            "e1 packets": lambda value: value["development"]["packets"]["e1"].pop(),
            "gold": lambda value: value["development"]["gold"].pop(),
            "audit": lambda value: value["development"]["audit"].pop(),
            "gold identity": lambda value: value["development"]["gold"][0].__setitem__(
                "question_id", "other-question"
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                corpus = copy.deepcopy(original)
                mutate(corpus)
                root = Path(directory)
                with self.assertRaisesRegex(ValueError, "development corpus"):
                    successor.materialize_development_corpus(
                        corpus,
                        public_root=root / "public",
                        audit_root=root / "audit",
                        generation_spec_sha256="1" * 64,
                        generation_receipt_sha256="2" * 64,
                        power_receipt_sha256="3" * 64,
                    )
                self.assertFalse((root / "public").exists())
                self.assertFalse((root / "audit").exists())

    def test_output_roots_cannot_alias_through_a_symlinked_parent(self) -> None:
        corpus = successor.construct_development_corpus(
            [_bundle(index, noise=0) for index in range(448)],
            power_receipt=POWER_RECEIPT,
            nonce_key=NONCE_KEY,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_parent = root / "public-parent"
            public_parent.mkdir()
            alias = root / "alias"
            alias.symlink_to(public_parent, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "real directory"):
                successor.materialize_development_corpus(
                    corpus,
                    public_root=public_parent / "public",
                    audit_root=alias / "public" / "audit",
                    generation_spec_sha256="1" * 64,
                    generation_receipt_sha256="2" * 64,
                    power_receipt_sha256="3" * 64,
                )

            real_nested = public_parent / "nested"
            real_nested.mkdir()
            with self.assertRaisesRegex(ValueError, "physically separate"):
                successor.materialize_development_corpus(
                    corpus,
                    public_root=public_parent / "nested",
                    audit_root=alias / "nested" / "audit",
                    generation_spec_sha256="1" * 64,
                    generation_receipt_sha256="2" * 64,
                    power_receipt_sha256="3" * 64,
                )


if __name__ == "__main__":
    unittest.main()
