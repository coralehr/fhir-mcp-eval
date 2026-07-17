from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from a11_evidence_core import canonical_bytes
from a11b_synthea_runner import (
    build_generation_spec,
    normalized_jdk_member,
)


ROOT = Path(__file__).resolve().parents[1]
POWER_SPEC = json.loads((ROOT / "fixtures" / "a11b_power_spec.json").read_text())
POWER_RECEIPT = json.loads(
    (ROOT / "docs" / "results" / "a11b-power-receipt.json").read_text()
)


class A11bSyntheaRunnerTests(unittest.TestCase):
    def test_build_spec_pins_every_runtime_input_and_nondeterministic_clock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {
                "generator/synthea.jar": b"jar",
                "runtime/Contents/Home/bin/java": b"java",
                "runtime/Contents/Home/lib/modules": b"modules",
                "runtime/java-version.txt": (
                    b"Temurin-21.0.11+10\nopenjdk version \"21.0.11\"\n"
                ),
                "modules/example.json": b'{"name":"example"}\n',
                "modules/example.csv": b"key,value\na,b\n",
                "configuration/synthea.properties": (
                    ROOT / "fixtures" / "a11b_synthea.properties"
                ).read_bytes(),
            }
            for relative, data in paths.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            (root / "runtime" / "Contents" / "Home" / "bin" / "java").chmod(
                0o755
            )

            spec = build_generation_spec(
                root,
                power_spec=POWER_SPEC,
                power_receipt=POWER_RECEIPT,
            )

            self.assertEqual(spec["generator"]["release_tag"], "v4.0.0")
            self.assertEqual(spec["generator"]["commit"], "0185c09ea9d10a822c6f5f3ef9bdcbcbe960c813")
            self.assertEqual(
                spec["invocation"]["argv"],
                [
                    "runtime/Contents/Home/bin/java",
                    "-jar",
                    "generator/synthea.jar",
                    "-s",
                    "20260716",
                    "-cs",
                    "20260716",
                    "-p",
                    "448",
                    "-r",
                    "20260715",
                    "-e",
                    "20260715",
                    "-c",
                    "configuration/synthea.properties",
                    "-d",
                    "modules",
                ],
            )
            self.assertEqual(len(spec["module_files"]), 2)
            self.assertEqual(len(spec["java_runtime"]["distribution_files"]), 1)
            self.assertEqual(spec["output"]["required_patient_count"], 448)
            self.assertEqual(spec["model_calls"], 0)
            self.assertEqual(
                canonical_bytes(spec),
                canonical_bytes(
                    build_generation_spec(
                        root,
                        power_spec=POWER_SPEC,
                        power_receipt=POWER_RECEIPT,
                    )
                ),
            )

    def test_jdk_archive_member_normalization_rejects_escape_and_links(self) -> None:
        import tarfile

        safe = tarfile.TarInfo("jdk-21.0.11+10/Contents/Home/bin/java")
        safe.type = tarfile.REGTYPE
        self.assertEqual(
            normalized_jdk_member(safe),
            Path("Contents/Home/bin/java"),
        )

        for name in (
            "../escape",
            "/absolute",
            "jdk-21.0.11+10/../../escape",
            "second-root/Contents/Home/bin/java",
        ):
            with self.subTest(name=name):
                member = tarfile.TarInfo(name)
                member.type = tarfile.REGTYPE
                with self.assertRaises(ValueError):
                    normalized_jdk_member(member)

        link = tarfile.TarInfo("jdk-21.0.11+10/Contents/Home/bin/java")
        link.type = tarfile.SYMTYPE
        link.linkname = "/bin/sh"
        with self.assertRaises(ValueError):
            normalized_jdk_member(link)

    def test_build_spec_rejects_symlinked_or_unexpected_stage_files(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "generator").mkdir()
            outside = root / "outside.jar"
            outside.write_bytes(b"jar")
            (root / "generator" / "synthea.jar").symlink_to(outside)

            with self.assertRaisesRegex(ValueError, "symlink"):
                build_generation_spec(
                    root,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )


if __name__ == "__main__":
    unittest.main()
