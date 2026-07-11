import tempfile
import unittest
from pathlib import Path

import run_a8_skill_matrix as a8


class A8SkillMatrixTests(unittest.TestCase):
    def test_builds_four_prompt_condition_arms(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill.md"
            skill.write_text("# FHIR skill\nUse date windows and source ids.\n", encoding="utf-8")
            controls = a8.write_control_skill_files(root / "controls", skill)
            arms = a8.build_arms(fhir_skill_file=skill, control_files=controls)

        self.assertEqual([arm.arm_id for arm in arms], ["A8-F0", "A8-FL", "A8-FP", "A8-FS"])
        self.assertIsNone(arms[0].skill_file)
        self.assertEqual(arms[-1].skill_file, skill)

    def test_generated_controls_are_length_matched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill.md"
            skill.write_text("FHIR retrieval guidance.\n" * 30, encoding="utf-8")
            controls = a8.write_control_skill_files(root / "controls", skill)
            target_len = len(skill.read_text(encoding="utf-8"))

            neutral_len = len(controls["neutral_length"].read_text(encoding="utf-8"))
            placebo_len = len(controls["placebo"].read_text(encoding="utf-8"))

        self.assertLessEqual(abs(neutral_len - target_len), 1)
        self.assertLessEqual(abs(placebo_len - target_len), 1)

    def test_harness_command_uses_packet_mode_and_skill_file_only_when_present(self):
        arm = a8.SkillArm("A8-FS", "fhir_skill", "FHIR retrieval skill", Path("skill.md"), "desc")
        cmd = a8.build_codex_harness_command(
            arm,
            input_path=Path("input.csv"),
            packet_json=Path("packets.jsonl"),
            out_dir=Path("runs/a8"),
            schema=Path("schema.json"),
            cwd=Path("/tmp/repo"),
            model="gpt-test",
            profile=None,
            substrate="codex_subscription",
            codex_bin="codex",
            sandbox="read-only",
            approval="never",
            limit=2,
            timeout=10,
            dry_run=True,
            live=False,
            allow_full_run=False,
            allow_public_artifact=False,
            skip_existing=False,
        )

        self.assertIn("--mode", cmd)
        self.assertIn("packet", cmd)
        self.assertIn("--packet-json", cmd)
        self.assertIn("packets.jsonl", cmd)
        self.assertIn("--skill-file", cmd)
        self.assertIn("skill.md", cmd)
        self.assertIn("--dry-run", cmd)


if __name__ == "__main__":
    unittest.main()
