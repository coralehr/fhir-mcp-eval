import json
import tempfile
import unittest
from pathlib import Path

from run_lock import AlreadyRunning, acquire_single_instance
from token_stats import usage_for_event_log


class RunLockTest(unittest.TestCase):
    def test_second_holder_is_rejected_until_first_releases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "pipeline.lock"

            with acquire_single_instance(lock_path):
                with self.assertRaises(AlreadyRunning):
                    with acquire_single_instance(lock_path):
                        self.fail("a second holder must never enter")

            with acquire_single_instance(lock_path):
                pass


class TokenStatsTest(unittest.TestCase):
    def test_uses_one_valid_completed_turn_and_ignores_collision_debris(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "events.jsonl"
            lines = [
                {"type": "thread.started", "thread_id": "first"},
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 100, "output_tokens": 5},
                },
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 900, "output_tokens": 90},
                },
            ]
            event_path.write_text(
                "\n".join(json.dumps(line) for line in lines)
                + '\nput_tokens": 800, "output_tokens": 80}}\n',
                encoding="utf-8",
            )

            self.assertEqual(usage_for_event_log(event_path), (100, 5))


if __name__ == "__main__":
    unittest.main()
