import unittest
from pathlib import Path

import run_a9_mcp_matrix as a9


class A9McpMatrixTests(unittest.TestCase):
    def test_builds_four_tool_skill_arms(self):
        arms = a9.build_arms(fhir_skill_file=Path("skill.md"), generic_subset="control", bonfire_subset="arm_full8")

        self.assertEqual([arm.arm_id for arm in arms], ["A9-T0", "A9-TSg", "A9-Tb", "A9-Tbs"])
        self.assertEqual([arm.tool_subset for arm in arms], ["control", "control", "arm_full8", "arm_full8"])
        self.assertEqual(arms[2].slug, "expanded_read_mcp")
        self.assertIsNone(arms[0].skill_file)
        self.assertEqual(arms[1].skill_file, Path("skill.md"))
        self.assertIsNone(arms[2].skill_file)
        self.assertEqual(arms[3].skill_file, Path("skill.md"))

    def test_harness_command_uses_mcp_mode_and_server_name(self):
        arm = a9.McpArm("A9-Tbs", "bonfire_mcp_skill", "Bonfire + skill", "arm_full8", Path("skill.md"), "desc")
        cmd = a9.build_codex_harness_command(
            arm,
            input_path=Path("input.csv"),
            out_dir=Path("runs/a9"),
            schema=Path("schema.json"),
            cwd=Path("/tmp/repo"),
            mcp_server_name="bonfire-eval",
            model=None,
            profile=None,
            substrate="codex_subscription",
            codex_bin="codex",
            sandbox="read-only",
            approval="never",
            limit=1,
            timeout=10,
            dry_run=True,
            live=False,
            allow_full_run=False,
            allow_public_artifact=False,
            skip_existing=False,
        )

        self.assertIn("--mode", cmd)
        self.assertIn("mcp", cmd)
        self.assertIn("--mcp-server-name", cmd)
        self.assertIn("bonfire-eval", cmd)
        self.assertIn("--skill-file", cmd)
        self.assertIn("skill.md", cmd)

    def test_server_env_selects_tool_subset_and_port(self):
        arm = a9.McpArm("A9-T0", "generic_mcp", "Generic", "control", None, "desc")
        env = a9.build_server_env(
            {"KEEP": "yes", "PATH": "/bin", "ANTHROPIC_API_KEY": "secret", "MEDPLUM_EMAIL": "admin@example.com"},
            arm=arm,
            medplum_base_url="http://localhost:8103",
            port=9000,
        )

        self.assertNotIn("KEEP", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env["MEDPLUM_EMAIL"], "admin@example.com")
        self.assertEqual(env["TOOL_SUBSET"], "control")
        self.assertEqual(env["TREATMENT_PORT"], "9000")
        self.assertEqual(env["MEDPLUM_BASE_URL"], "http://localhost:8103")


if __name__ == "__main__":
    unittest.main()
