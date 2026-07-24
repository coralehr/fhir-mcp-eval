import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import source_provenance


class SourceProvenanceTests(unittest.TestCase):
    def test_receipt_is_deterministic_and_binds_the_source_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            source = repo / "runner.py"
            source.write_text("print('v1')\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "runner.py"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )

            first = source_provenance.build_receipt(repo)
            second = source_provenance.build_receipt(repo)
            source.write_text("print('v2')\n", encoding="utf-8")
            changed = source_provenance.build_receipt(repo)

            self.assertEqual(first, second)
            self.assertFalse(first["source_dirty"])
            self.assertRegex(first["source_commit"], r"^[0-9a-f]{40}$")
            self.assertRegex(
                first["source_manifest_sha256"], r"^[0-9a-f]{64}$"
            )
            self.assertTrue(changed["source_dirty"])
            self.assertNotEqual(
                changed["source_manifest_sha256"],
                first["source_manifest_sha256"],
            )

    def test_cli_writes_one_canonical_receipt_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            repo.joinpath("runner.py").write_text("pass\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "runner.py"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            output = root / "source-provenance.json"

            result = source_provenance.main(
                ["--repo", str(repo), "--out", str(output)]
            )

            self.assertEqual(result, 0)
            payload = output.read_bytes()
            receipt = json.loads(payload)
            self.assertEqual(
                payload,
                json.dumps(
                    receipt, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                + b"\n",
            )

    def test_deleted_tracked_file_is_bound_as_a_dirty_current_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            deleted = repo / "deleted.py"
            retained = repo / "retained.py"
            deleted.write_text("obsolete = True\n", encoding="utf-8")
            retained.write_text("current = True\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            before = source_provenance.build_receipt(repo)
            deleted.unlink()

            after = source_provenance.build_receipt(repo)

            self.assertTrue(after["source_dirty"])
            self.assertNotEqual(
                after["source_manifest_sha256"],
                before["source_manifest_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
