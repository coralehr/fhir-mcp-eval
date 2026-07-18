from __future__ import annotations

import unittest

import a11b_launch_protocol as protocol


class A11bLaunchProtocolTests(unittest.TestCase):
    def test_readiness_is_exact_zero_call_state(self) -> None:
        ready = protocol.readiness_status(
            run_id="a" * 64,
            schedule_length=192,
            updated_at="2026-07-18T00:00:00Z",
        )
        self.assertEqual(
            protocol.validate_readiness(
                ready, run_id="a" * 64, schedule_length=192
            ),
            ready,
        )
        for field, replacement in (
            ("schedule_position", 1),
            ("model_calls_reserved", 1),
            ("model_calls_closed", 1),
            ("run_id", "b" * 64),
            ("schedule_length", 1152),
        ):
            changed = dict(ready)
            changed[field] = replacement
            with self.subTest(field=field), self.assertRaises(ValueError):
                protocol.validate_readiness(
                    changed, run_id="a" * 64, schedule_length=192
                )

    def test_acknowledgement_and_confirmation_bind_every_identity(self) -> None:
        acknowledgement = protocol.acknowledgement(
            run_id="a" * 64,
            controller_sha256="b" * 64,
            schedule_length=192,
            ready_status_sha256="c" * 64,
        )
        confirmation = protocol.confirmation(
            run_id="a" * 64,
            controller_sha256="b" * 64,
            schedule_length=192,
            acknowledgement_sha256=protocol.sha256(
                protocol.canonical_json_line(acknowledgement)
            ),
        )
        commit = protocol.launch_commit(
            run_id="a" * 64,
            controller_sha256="b" * 64,
            schedule_length=192,
            confirmation_sha256=protocol.sha256(
                protocol.canonical_json_line(confirmation)
            ),
        )
        self.assertEqual(
            protocol.require_exact(acknowledgement, acknowledgement), acknowledgement
        )
        self.assertEqual(
            protocol.require_exact(confirmation, confirmation), confirmation
        )
        self.assertEqual(protocol.require_exact(commit, commit), commit)
        changed = dict(confirmation)
        changed["schedule_length"] = 1152
        with self.assertRaises(ValueError):
            protocol.require_exact(changed, confirmation)


if __name__ == "__main__":
    unittest.main()
