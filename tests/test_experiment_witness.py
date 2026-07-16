from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import experiment_witness as witness


TEST_COMMITMENT_KEY = bytes(range(32))


def digest(label: str) -> str:
    return witness.keyed_commitment(
        TEST_COMMITMENT_KEY,
        domain="test-receipt",
        payload=label.encode("utf-8"),
    )


class ExperimentWitnessTests(unittest.TestCase):
    def make_authenticator(
        self, root: Path
    ) -> witness.SshEd25519Authenticator:
        private_key = root / "witness-ed25519"
        subprocess.run(
            [
                str(witness.SSH_KEYGEN_PATH),
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "coralehr-test-witness",
                "-f",
                str(private_key),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return witness.SshEd25519Authenticator(
            private_key_path=private_key,
            identity="coralehr-test-witness",
        )

    def make_ledger(
        self, root: Path, authenticator: witness.SshEd25519Authenticator
    ) -> witness.WitnessLedger:
        return witness.WitnessLedger(
            root / "ledger",
            run_id=digest("anchored-controller-and-receipt"),
            schedule=(
                witness.ScheduleItem(
                    phase="answer",
                    schedule_index=0,
                    call_commitment=digest("answer-prompt-runtime"),
                    max_attempts=3,
                ),
                witness.ScheduleItem(
                    phase="panel",
                    schedule_index=0,
                    call_commitment=digest("panel-prompt-runtime"),
                    max_attempts=3,
                ),
            ),
            authenticator=authenticator,
            clock=lambda: "2026-07-15T18:00:00Z",
        )

    def test_clean_retries_restart_and_phase_transition_are_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authenticator = self.make_authenticator(root)
            ledger = self.make_ledger(root, authenticator)

            opened_1 = ledger.open_call(
                witness.CallDescriptor(
                    phase="answer",
                    schedule_index=0,
                    attempt_number=1,
                    call_commitment=digest("answer-prompt-runtime"),
                ),
                expected_head=witness.GENESIS_HEAD,
            )
            closed_1 = ledger.close_call(
                opened_receipt_sha256=witness.receipt_sha256(opened_1),
                outcome="provider_failure",
                artifact_root_commitment=digest("answer-attempt-1-artifacts"),
                token_usage={
                    "input": 10,
                    "cached": 0,
                    "output": 0,
                    "reasoning": 0,
                    "total": 10,
                    "complete": True,
                    "source": "provider.error",
                },
                expected_head=witness.receipt_sha256(opened_1),
            )
            opened_2 = ledger.open_call(
                witness.CallDescriptor(
                    phase="answer",
                    schedule_index=0,
                    attempt_number=2,
                    call_commitment=digest("answer-prompt-runtime"),
                ),
                expected_head=witness.receipt_sha256(closed_1),
            )
            closed_2 = ledger.close_call(
                opened_receipt_sha256=witness.receipt_sha256(opened_2),
                outcome="accepted",
                artifact_root_commitment=digest("answer-attempt-2-artifacts"),
                token_usage={
                    "input": 10,
                    "cached": 2,
                    "output": 4,
                    "reasoning": 1,
                    "total": 14,
                    "complete": True,
                    "source": "turn.completed",
                },
                expected_head=witness.receipt_sha256(opened_2),
            )
            opened_panel = ledger.open_call(
                witness.CallDescriptor(
                    phase="panel",
                    schedule_index=0,
                    attempt_number=1,
                    call_commitment=digest("panel-prompt-runtime"),
                ),
                expected_head=witness.receipt_sha256(closed_2),
            )
            closed_panel = ledger.close_call(
                opened_receipt_sha256=witness.receipt_sha256(opened_panel),
                outcome="accepted",
                artifact_root_commitment=digest("panel-artifacts"),
                token_usage={
                    "input": 20,
                    "cached": 5,
                    "output": 3,
                    "reasoning": 1,
                    "total": 23,
                    "complete": True,
                    "source": "turn.completed",
                },
                expected_head=witness.receipt_sha256(opened_panel),
            )

            restarted = self.make_ledger(root, authenticator)
            status = restarted.status()
            self.assertEqual(status["head"], witness.receipt_sha256(closed_panel))
            self.assertEqual(status["events"], 6)
            self.assertEqual(status["state"], "complete")
            self.assertEqual(status["model_calls_reserved"], 3)
            self.assertEqual(status["model_calls_closed"], 3)
            public_verifier = witness.SshEd25519Verifier(
                public_key=authenticator.public_key,
                identity=authenticator.identity,
            )
            self.assertEqual(public_verifier.key_id, authenticator.key_id)
            receipts = []
            for event_path in sorted((root / "ledger" / "events").glob("*.json")):
                receipt = json.loads(event_path.read_text())
                receipts.append(receipt)
                self.assertTrue(
                    public_verifier.verify_receipt(receipt)
                )
            public_chain = witness.WitnessChainVerifier(
                run_id=ledger.run_id,
                schedule=ledger.schedule,
                verifier=public_verifier,
            )
            self.assertEqual(
                public_chain.verify(
                    receipts, expected_head=witness.receipt_sha256(closed_panel)
                )["state"],
                "complete",
            )
            with self.assertRaisesRegex(
                witness.WitnessIntegrityError, "rolled back"
            ):
                public_chain.verify(
                    receipts[:-2],
                    expected_head=witness.receipt_sha256(closed_panel),
                )

    def test_public_key_is_one_normalized_ed25519_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.make_authenticator(root)
            second_root = root / "second"
            second_root.mkdir()
            second = self.make_authenticator(second_root)
            injected = first.public_key + "\nwitness " + second.public_key
            with self.assertRaisesRegex(witness.WitnessIntegrityError, "public key"):
                witness.SshEd25519Verifier(
                    public_key=injected,
                    identity="coralehr-test-witness",
                )

    def test_crypto_subprocesses_ignore_hostile_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authenticator = self.make_authenticator(root)
            with mock.patch.dict("os.environ", {"PATH": str(root)}, clear=False):
                ledger = self.make_ledger(root, authenticator)
                opened = ledger.open_call(
                    witness.CallDescriptor(
                        phase="answer",
                        schedule_index=0,
                        attempt_number=1,
                        call_commitment=digest("answer-prompt-runtime"),
                    ),
                    expected_head=witness.GENESIS_HEAD,
                )
            self.assertTrue(authenticator.verify_receipt(opened))

    def test_public_commitments_are_keyed_and_domain_separated(self) -> None:
        payload = b"low entropy clinical code 123"
        raw_sha256 = hashlib.sha256(payload).hexdigest()
        first = witness.keyed_commitment(
            TEST_COMMITMENT_KEY, domain="answer-call", payload=payload
        )
        second = witness.keyed_commitment(
            b"x" * 32, domain="answer-call", payload=payload
        )
        other_domain = witness.keyed_commitment(
            TEST_COMMITMENT_KEY, domain="artifact-root", payload=payload
        )
        self.assertNotEqual(first, raw_sha256)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, other_domain)

    def test_token_receipts_distinguish_complete_usage_from_unknown_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = self.make_ledger(root, self.make_authenticator(root))
            opened = ledger.open_call(
                witness.CallDescriptor(
                    phase="answer",
                    schedule_index=0,
                    attempt_number=1,
                    call_commitment=digest("answer-prompt-runtime"),
                ),
                expected_head=witness.GENESIS_HEAD,
            )
            closed = ledger.close_call(
                opened_receipt_sha256=witness.receipt_sha256(opened),
                outcome="provider_failure",
                artifact_root_commitment=digest("provider-failure-artifacts"),
                token_usage={
                    "input": None,
                    "cached": None,
                    "output": None,
                    "reasoning": None,
                    "total": None,
                    "complete": False,
                    "source": "unavailable",
                },
                expected_head=witness.receipt_sha256(opened),
            )
            self.assertFalse(closed["body"]["token_usage"]["complete"])
            self.assertIsNone(closed["body"]["token_usage"]["total"])

            opened_retry = ledger.open_call(
                witness.CallDescriptor(
                    phase="answer",
                    schedule_index=0,
                    attempt_number=2,
                    call_commitment=digest("answer-prompt-runtime"),
                ),
                expected_head=witness.receipt_sha256(closed),
            )
            with self.assertRaisesRegex(
                witness.WitnessProtocolError, "accepted token usage must be complete"
            ):
                ledger.close_call(
                    opened_receipt_sha256=witness.receipt_sha256(opened_retry),
                    outcome="accepted",
                    artifact_root_commitment=digest("invalid-accepted-artifacts"),
                    token_usage={
                        "input": None,
                        "cached": None,
                        "output": None,
                        "reasoning": None,
                        "total": None,
                        "complete": False,
                        "source": "unavailable",
                    },
                    expected_head=witness.receipt_sha256(opened_retry),
                )

    def test_open_and_close_are_idempotent_after_lost_ack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = self.make_ledger(root, self.make_authenticator(root))
            descriptor = witness.CallDescriptor(
                phase="answer",
                schedule_index=0,
                attempt_number=1,
                call_commitment=digest("answer-prompt-runtime"),
            )
            opened = ledger.open_call(
                descriptor, expected_head=witness.GENESIS_HEAD
            )
            self.assertEqual(
                ledger.open_call(descriptor, expected_head=witness.GENESIS_HEAD),
                opened,
            )
            restarted_after_open = self.make_ledger(root, ledger.authenticator)
            self.assertEqual(
                restarted_after_open.open_call(
                    descriptor, expected_head=witness.GENESIS_HEAD
                ),
                opened,
            )
            close_kwargs = {
                "opened_receipt_sha256": witness.receipt_sha256(opened),
                "outcome": "accepted",
                "artifact_root_commitment": digest("artifacts"),
                "token_usage": {
                    "input": 4,
                    "cached": 0,
                    "output": 2,
                    "reasoning": 1,
                    "total": 6,
                    "complete": True,
                    "source": "turn.completed",
                },
                "expected_head": witness.receipt_sha256(opened),
            }
            closed = restarted_after_open.close_call(**close_kwargs)
            restarted_after_close = self.make_ledger(root, ledger.authenticator)
            self.assertEqual(restarted_after_close.close_call(**close_kwargs), closed)
            self.assertEqual(restarted_after_close.status()["events"], 2)

    def test_partial_staged_event_never_becomes_a_committed_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = self.make_ledger(root, self.make_authenticator(root))
            descriptor = witness.CallDescriptor(
                phase="answer",
                schedule_index=0,
                attempt_number=1,
                call_commitment=digest("answer-prompt-runtime"),
            )
            self.assertEqual(ledger.status()["head"], witness.GENESIS_HEAD)
            real_write_all = witness.WitnessLedger._write_all

            def partial_write(file_descriptor: int, payload: bytes) -> None:
                os.write(file_descriptor, payload[:17])
                os.fsync(file_descriptor)
                raise OSError("synthetic crash during event staging")

            with (
                mock.patch.object(
                    witness.WitnessLedger,
                    "_write_all",
                    side_effect=partial_write,
                ),
                self.assertRaisesRegex(OSError, "synthetic crash"),
            ):
                ledger.open_call(descriptor, expected_head=witness.GENESIS_HEAD)

            self.assertEqual(list((root / "ledger" / "events").iterdir()), [])
            self.assertEqual(
                (root / "ledger" / "HEAD").read_text().strip(),
                witness.GENESIS_HEAD,
            )
            with mock.patch.object(
                witness.WitnessLedger,
                "_write_all",
                side_effect=real_write_all,
            ):
                opened = ledger.open_call(
                    descriptor, expected_head=witness.GENESIS_HEAD
                )
            self.assertEqual(opened["body"]["seq"], 0)

    def test_event_publish_before_head_ack_recovers_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authenticator = self.make_authenticator(root)
            ledger = self.make_ledger(root, authenticator)
            descriptor = witness.CallDescriptor(
                phase="answer",
                schedule_index=0,
                attempt_number=1,
                call_commitment=digest("answer-prompt-runtime"),
            )
            with (
                mock.patch.object(
                    ledger,
                    "_write_head",
                    side_effect=OSError("synthetic crash before HEAD update"),
                ),
                self.assertRaisesRegex(OSError, "before HEAD"),
            ):
                ledger.open_call(descriptor, expected_head=witness.GENESIS_HEAD)

            restarted = self.make_ledger(root, authenticator)
            recovered = restarted.open_call(
                descriptor, expected_head=witness.GENESIS_HEAD
            )
            self.assertEqual(recovered["body"]["seq"], 0)
            self.assertEqual(restarted.status()["events"], 1)

    def test_unresolved_open_wrong_head_and_conflicting_close_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = self.make_ledger(root, self.make_authenticator(root))
            descriptor = witness.CallDescriptor(
                phase="answer",
                schedule_index=0,
                attempt_number=1,
                call_commitment=digest("answer-prompt-runtime"),
            )
            opened = ledger.open_call(
                descriptor, expected_head=witness.GENESIS_HEAD
            )
            with self.assertRaisesRegex(witness.WitnessProtocolError, "unresolved"):
                ledger.open_call(
                    witness.CallDescriptor(
                        phase="answer",
                        schedule_index=0,
                        attempt_number=2,
                        call_commitment=digest("answer-prompt-runtime"),
                    ),
                    expected_head=witness.receipt_sha256(opened),
                )
            with self.assertRaisesRegex(witness.WitnessProtocolError, "head"):
                ledger.close_call(
                    opened_receipt_sha256=witness.receipt_sha256(opened),
                    outcome="accepted",
                    artifact_root_commitment=digest("artifacts"),
                    token_usage={
                        "input": 1,
                        "cached": 0,
                        "output": 1,
                        "reasoning": 0,
                        "total": 2,
                        "complete": True,
                        "source": "turn.completed",
                    },
                    expected_head=digest("stale-head"),
                )
            closed = ledger.close_call(
                opened_receipt_sha256=witness.receipt_sha256(opened),
                outcome="accepted",
                artifact_root_commitment=digest("artifacts"),
                token_usage={
                    "input": 1,
                    "cached": 0,
                    "output": 1,
                    "reasoning": 0,
                    "total": 2,
                    "complete": True,
                    "source": "turn.completed",
                },
                expected_head=witness.receipt_sha256(opened),
            )
            with self.assertRaisesRegex(witness.WitnessProtocolError, "conflicting"):
                ledger.close_call(
                    opened_receipt_sha256=witness.receipt_sha256(opened),
                    outcome="accepted",
                    artifact_root_commitment=digest("different-artifacts"),
                    token_usage={
                        "input": 1,
                        "cached": 0,
                        "output": 1,
                        "reasoning": 0,
                        "total": 2,
                        "complete": True,
                        "source": "turn.completed",
                    },
                    expected_head=witness.receipt_sha256(opened),
                )
            self.assertEqual(ledger.status()["head"], witness.receipt_sha256(closed))

    def test_retry_cap_and_indeterminate_outcome_are_terminal(self) -> None:
        for terminal_outcome in ("contaminated", "indeterminate"):
            with self.subTest(outcome=terminal_outcome):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    ledger = self.make_ledger(root, self.make_authenticator(root))
                    opened = ledger.open_call(
                        witness.CallDescriptor(
                            phase="answer",
                            schedule_index=0,
                            attempt_number=1,
                            call_commitment=digest("answer-prompt-runtime"),
                        ),
                        expected_head=witness.GENESIS_HEAD,
                    )
                    closed = ledger.close_call(
                        opened_receipt_sha256=witness.receipt_sha256(opened),
                        outcome=terminal_outcome,
                        artifact_root_commitment=digest("terminal-artifacts"),
                        token_usage={
                            "input": None,
                            "cached": None,
                            "output": None,
                            "reasoning": None,
                            "total": None,
                            "complete": False,
                            "source": "unavailable",
                        },
                        expected_head=witness.receipt_sha256(opened),
                    )
                    self.assertEqual(ledger.status()["state"], "aborted")
                    with self.assertRaisesRegex(
                        witness.WitnessProtocolError, "aborted"
                    ):
                        ledger.open_call(
                            witness.CallDescriptor(
                                phase="answer",
                                schedule_index=0,
                                attempt_number=2,
                                call_commitment=digest("answer-prompt-runtime"),
                            ),
                            expected_head=witness.receipt_sha256(closed),
                        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = self.make_ledger(root, self.make_authenticator(root))
            head = witness.GENESIS_HEAD
            for attempt_number in range(1, 4):
                opened = ledger.open_call(
                    witness.CallDescriptor(
                        phase="answer",
                        schedule_index=0,
                        attempt_number=attempt_number,
                        call_commitment=digest("answer-prompt-runtime"),
                    ),
                    expected_head=head,
                )
                closed = ledger.close_call(
                    opened_receipt_sha256=witness.receipt_sha256(opened),
                    outcome="provider_failure",
                    artifact_root_commitment=digest(f"failure-{attempt_number}"),
                    token_usage={
                        "input": None,
                        "cached": None,
                        "output": None,
                        "reasoning": None,
                        "total": None,
                        "complete": False,
                        "source": "unavailable",
                    },
                    expected_head=witness.receipt_sha256(opened),
                )
                head = witness.receipt_sha256(closed)
            self.assertEqual(ledger.status()["state"], "aborted")
            with self.assertRaisesRegex(witness.WitnessProtocolError, "aborted"):
                ledger.open_call(
                    witness.CallDescriptor(
                        phase="answer",
                        schedule_index=0,
                        attempt_number=4,
                        call_commitment=digest("answer-prompt-runtime"),
                    ),
                    expected_head=head,
                )

    def test_schedule_commitment_substitution_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = self.make_ledger(root, self.make_authenticator(root))
            for descriptor in (
                witness.CallDescriptor(
                    phase="panel",
                    schedule_index=0,
                    attempt_number=1,
                    call_commitment=digest("panel-prompt-runtime"),
                ),
                witness.CallDescriptor(
                    phase="answer",
                    schedule_index=0,
                    attempt_number=1,
                    call_commitment=digest("substituted-prompt"),
                ),
            ):
                with self.subTest(descriptor=descriptor):
                    with self.assertRaisesRegex(
                        witness.WitnessProtocolError, "schedule"
                    ):
                        ledger.open_call(
                            descriptor, expected_head=witness.GENESIS_HEAD
                        )

    def test_tamper_truncation_and_reordering_fail_replay(self) -> None:
        attacks = ("tamper", "truncate", "reorder")
        for attack in attacks:
            with self.subTest(attack=attack):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    authenticator = self.make_authenticator(root)
                    ledger = self.make_ledger(root, authenticator)
                    opened = ledger.open_call(
                        witness.CallDescriptor(
                            phase="answer",
                            schedule_index=0,
                            attempt_number=1,
                            call_commitment=digest("answer-prompt-runtime"),
                        ),
                        expected_head=witness.GENESIS_HEAD,
                    )
                    ledger.close_call(
                        opened_receipt_sha256=witness.receipt_sha256(opened),
                        outcome="accepted",
                        artifact_root_commitment=digest("artifacts"),
                        token_usage={
                            "input": 1,
                            "cached": 0,
                            "output": 1,
                            "reasoning": 0,
                            "total": 2,
                            "complete": True,
                            "source": "turn.completed",
                        },
                        expected_head=witness.receipt_sha256(opened),
                    )
                    events = sorted((root / "ledger" / "events").glob("*.json"))
                    for event in events:
                        event.chmod(0o600)
                    if attack == "tamper":
                        payload = json.loads(events[0].read_text())
                        payload["body"]["attempt_number"] = 99
                        events[0].write_text(json.dumps(payload) + "\n")
                    elif attack == "truncate":
                        events[1].write_bytes(events[1].read_bytes()[:20])
                    else:
                        first = events[0].read_bytes()
                        second = events[1].read_bytes()
                        events[0].write_bytes(second)
                        events[1].write_bytes(first)
                    with self.assertRaises(witness.WitnessIntegrityError):
                        self.make_ledger(root, authenticator).status()

    def test_head_cache_loss_is_rebuilt_from_signed_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authenticator = self.make_authenticator(root)
            ledger = self.make_ledger(root, authenticator)
            opened = ledger.open_call(
                witness.CallDescriptor(
                    phase="answer",
                    schedule_index=0,
                    attempt_number=1,
                    call_commitment=digest("answer-prompt-runtime"),
                ),
                expected_head=witness.GENESIS_HEAD,
            )
            head_path = root / "ledger" / "HEAD"
            head_path.unlink()
            status = self.make_ledger(root, authenticator).status()
            self.assertEqual(status["head"], witness.receipt_sha256(opened))
            self.assertEqual(head_path.read_text().strip(), status["head"])

    def test_strict_prefix_head_repairs_but_divergent_head_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authenticator = self.make_authenticator(root)
            ledger = self.make_ledger(root, authenticator)
            opened = ledger.open_call(
                witness.CallDescriptor(
                    phase="answer",
                    schedule_index=0,
                    attempt_number=1,
                    call_commitment=digest("answer-prompt-runtime"),
                ),
                expected_head=witness.GENESIS_HEAD,
            )
            closed = ledger.close_call(
                opened_receipt_sha256=witness.receipt_sha256(opened),
                outcome="accepted",
                artifact_root_commitment=digest("artifacts"),
                token_usage={
                    "input": 1,
                    "cached": 0,
                    "output": 1,
                    "reasoning": 0,
                    "total": 2,
                    "complete": True,
                    "source": "turn.completed",
                },
                expected_head=witness.receipt_sha256(opened),
            )
            head_path = root / "ledger" / "HEAD"
            head_path.write_text(witness.receipt_sha256(opened) + "\n")
            status = self.make_ledger(root, authenticator).status()
            self.assertEqual(status["head"], witness.receipt_sha256(closed))
            self.assertEqual(head_path.read_text().strip(), status["head"])

            head_path.write_text(digest("divergent-head") + "\n")
            with self.assertRaisesRegex(
                witness.WitnessIntegrityError, "HEAD differs"
            ):
                self.make_ledger(root, authenticator).status()

    def test_concurrent_identical_open_reserves_exactly_one_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = self.make_ledger(root, self.make_authenticator(root))
            descriptor = witness.CallDescriptor(
                phase="answer",
                schedule_index=0,
                attempt_number=1,
                call_commitment=digest("answer-prompt-runtime"),
            )

            def reserve() -> dict:
                return ledger.open_call(
                    descriptor, expected_head=witness.GENESIS_HEAD
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                receipts = list(executor.map(lambda _index: reserve(), range(2)))
            self.assertEqual(receipts[0], receipts[1])
            self.assertEqual(ledger.status()["events"], 1)
            self.assertEqual(ledger.status()["model_calls_reserved"], 1)

    def test_two_first_use_instances_share_one_initialized_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authenticator = self.make_authenticator(root)

            def initialize(_index: int) -> witness.WitnessLedger:
                return self.make_ledger(root, authenticator)

            with ThreadPoolExecutor(max_workers=2) as executor:
                ledgers = list(executor.map(initialize, range(2)))
            self.assertEqual(ledgers[0].status(), ledgers[1].status())

    def test_symlink_root_and_lock_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authenticator = self.make_authenticator(root)
            real_root = root / "real-ledger"
            linked_root = root / "linked-ledger"
            real_root.mkdir(mode=0o700)
            linked_root.symlink_to(real_root, target_is_directory=True)
            with self.assertRaisesRegex(witness.WitnessIntegrityError, "symlink"):
                witness.WitnessLedger(
                    linked_root,
                    run_id=digest("run"),
                    schedule=(
                        witness.ScheduleItem(
                            phase="answer",
                            schedule_index=0,
                            call_commitment=digest("call"),
                            max_attempts=1,
                        ),
                    ),
                    authenticator=authenticator,
                    clock=lambda: "2026-07-15T18:00:00Z",
                )

            ledger = self.make_ledger(root, authenticator)
            lock_path = root / "ledger" / ".lock"
            lock_path.unlink()
            target = root / "lock-target"
            target.write_bytes(b"")
            lock_path.symlink_to(target)
            with self.assertRaisesRegex(witness.WitnessIntegrityError, "lock"):
                ledger.status()

    def test_invalid_token_economics_never_close_a_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = self.make_ledger(root, self.make_authenticator(root))
            opened = ledger.open_call(
                witness.CallDescriptor(
                    phase="answer",
                    schedule_index=0,
                    attempt_number=1,
                    call_commitment=digest("answer-prompt-runtime"),
                ),
                expected_head=witness.GENESIS_HEAD,
            )
            with self.assertRaisesRegex(witness.WitnessProtocolError, "token"):
                ledger.close_call(
                    opened_receipt_sha256=witness.receipt_sha256(opened),
                    outcome="accepted",
                    artifact_root_commitment=digest("artifacts"),
                    token_usage={
                        "input": 5,
                        "cached": 6,
                        "output": 1,
                        "reasoning": 0,
                        "total": 6,
                        "complete": True,
                        "source": "turn.completed",
                    },
                    expected_head=witness.receipt_sha256(opened),
                )
            self.assertEqual(ledger.status()["state"], "open")

    def test_invalid_clock_never_publishes_a_witness_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authenticator = self.make_authenticator(root)
            ledger = self.make_ledger(root, authenticator)
            ledger.clock = lambda: ""
            descriptor = witness.CallDescriptor(
                phase="answer",
                schedule_index=0,
                attempt_number=1,
                call_commitment=digest("answer-prompt-runtime"),
            )
            with self.assertRaisesRegex(
                witness.WitnessIntegrityError, "timestamp"
            ):
                ledger.open_call(
                    descriptor, expected_head=witness.GENESIS_HEAD
                )
            self.assertEqual(list((root / "ledger" / "events").iterdir()), [])

            restarted = self.make_ledger(root, authenticator)
            opened = restarted.open_call(
                descriptor, expected_head=witness.GENESIS_HEAD
            )
            self.assertEqual(opened["body"]["seq"], 0)


if __name__ == "__main__":
    unittest.main()
