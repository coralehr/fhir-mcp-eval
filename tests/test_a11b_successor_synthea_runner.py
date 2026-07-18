from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import a11b_successor_synthea_runner as runner


ROOT = Path(__file__).resolve().parents[1]
POWER_SPEC = json.loads((ROOT / "fixtures" / "a11b_power_spec.json").read_text())
POWER_RECEIPT = json.loads(
    (ROOT / "docs" / "results" / "a11b-power-receipt.json").read_text()
)


def staged_root(root: Path) -> Path:
    files = {
        "generator/synthea.jar": b"jar",
        "runtime/Contents/Home/bin/java": b"java",
        "runtime/Contents/Home/lib/modules": b"modules",
        "runtime/java-version.txt": (
            b"Temurin-21.0.11+10\nopenjdk version \"21.0.11\"\n"
        ),
        "modules/example.json": b'{"name":"example"}\n',
        "configuration/synthea.properties": (
            ROOT / "fixtures" / "a11b_synthea.properties"
        ).read_bytes(),
    }
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    (root / runner.JAVA_EXECUTABLE).chmod(0o755)
    return root


class A11bSuccessorSyntheaRunnerTests(unittest.TestCase):
    def test_successor_plan_changes_population_identity_without_changing_upstream(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spec = runner.build_generation_spec(
                staged_root(Path(directory)),
                power_spec=POWER_SPEC,
                power_receipt=POWER_RECEIPT,
            )

        self.assertEqual(spec["generator"]["release_tag"], "v4.0.0")
        self.assertEqual(
            spec["generator"]["jar"]["sha256"],
            hashlib.sha256(b"jar").hexdigest(),
        )
        self.assertEqual(spec["invocation"]["seed"], 20260718)
        self.assertEqual(spec["invocation"]["reference_date"], "2026-07-17")
        self.assertEqual(spec["invocation"]["population"], 448)
        self.assertEqual(spec["output"]["max_file_bytes"], 128 * 1024 * 1024)
        self.assertEqual(
            spec["invocation"]["argv"][4:8],
            ["20260718", "-cs", "20260718", "-p"],
        )
        self.assertEqual(spec["model_calls"], 0)

    def test_stage_generation_creates_a_new_exact_input_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar = root / "source.jar"
            archive = root / "runtime.tar.gz"
            checkout = root / "synthea"
            artifact = root / "successor-root"
            jar.write_bytes(b"jar")
            archive.write_bytes(b"archive")
            checkout.mkdir()

            def fake_extract(_archive: Path, destination: Path) -> None:
                java = destination / runner.JAVA_EXECUTABLE.removeprefix(
                    "runtime/"
                )
                java.parent.mkdir(parents=True)
                java.write_bytes(b"java")
                java.chmod(0o755)
                modules = destination / "Contents/Home/lib/modules"
                modules.parent.mkdir(parents=True)
                modules.write_bytes(b"runtime-modules")

            def fake_copy(_source: Path, destination: Path) -> None:
                destination.mkdir(parents=True)
                (destination / "example.json").write_text('{"name":"example"}')

            process = mock.Mock(returncode=0, stdout=b"", stderr=b"Temurin 21.0.11")
            with mock.patch.object(
                runner.historical, "_verify_upstream"
            ), mock.patch.object(
                runner.historical, "_extract_jdk", side_effect=fake_extract
            ), mock.patch.object(
                runner.historical, "_copy_tree", side_effect=fake_copy
            ), mock.patch.object(
                runner.historical.subprocess,
                "run",
                return_value=process,
            ):
                spec = runner.stage_generation(
                    synthea_jar=jar,
                    jdk_archive=archive,
                    synthea_checkout=checkout,
                    artifact_root=artifact,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

            self.assertTrue((artifact / "generator/synthea.jar").is_file())
            self.assertEqual(spec["invocation"]["seed"], 20260718)
            with self.assertRaisesRegex(ValueError, "already exists"):
                runner.stage_generation(
                    synthea_jar=jar,
                    jdk_archive=archive,
                    synthea_checkout=checkout,
                    artifact_root=artifact,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

    def test_stage_generation_leaves_no_published_root_when_spec_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jar = root / "source.jar"
            archive = root / "runtime.tar.gz"
            checkout = root / "synthea"
            artifact = root / "successor-root"
            jar.write_bytes(b"jar")
            archive.write_bytes(b"archive")
            checkout.mkdir()

            def fake_extract(_archive: Path, destination: Path) -> None:
                java = destination / runner.JAVA_EXECUTABLE.removeprefix(
                    "runtime/"
                )
                java.parent.mkdir(parents=True)
                java.write_bytes(b"java")
                java.chmod(0o755)

            def fake_copy(_source: Path, destination: Path) -> None:
                destination.mkdir(parents=True)
                (destination / "example.json").write_text("{}")

            process = mock.Mock(returncode=0, stdout=b"", stderr=b"Temurin 21.0.11")
            with mock.patch.object(
                runner.historical, "_verify_upstream"
            ), mock.patch.object(
                runner.historical, "_extract_jdk", side_effect=fake_extract
            ), mock.patch.object(
                runner.historical, "_copy_tree", side_effect=fake_copy
            ), mock.patch.object(
                runner.historical.subprocess, "run", return_value=process
            ), mock.patch.object(
                runner, "build_generation_spec", side_effect=ValueError("bad spec")
            ):
                with self.assertRaisesRegex(ValueError, "bad spec"):
                    runner.stage_generation(
                        synthea_jar=jar,
                        jdk_archive=archive,
                        synthea_checkout=checkout,
                        artifact_root=artifact,
                        power_spec=POWER_SPEC,
                        power_receipt=POWER_RECEIPT,
                    )

            self.assertFalse(artifact.exists())
            self.assertEqual(list(root.glob(".successor-root.staging-*")), [])

    def test_run_generation_accepts_only_the_exact_successor_spec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = staged_root(Path(directory))
            spec = runner.build_generation_spec(
                artifact,
                power_spec=POWER_SPEC,
                power_receipt=POWER_RECEIPT,
            )

            def fake_run(*_args, **_kwargs):
                output = artifact / runner.OUTPUT_ROOT
                output.mkdir()
                (output / "patient.json").write_text('{"resourceType":"Bundle"}')
                return mock.Mock(returncode=0, stdout=b"generated", stderr=b"")

            with mock.patch.object(
                runner.historical.subprocess,
                "run",
                side_effect=fake_run,
            ) as subprocess_run, mock.patch.object(
                runner.historical,
                "compile_generation_receipt",
                return_value={"schema_version": "receipt", "model_calls": 0},
            ), mock.patch.object(
                runner.historical, "_freeze_tree"
            ):
                receipt, log = runner.run_generation(
                    spec,
                    artifact_root=artifact,
                    power_spec=POWER_SPEC,
                    power_receipt=POWER_RECEIPT,
                )

        self.assertEqual(receipt["model_calls"], 0)
        self.assertEqual(log, b"generated")
        self.assertEqual(
            subprocess_run.call_args.kwargs["timeout"],
            runner.GENERATION_TIMEOUT_SECONDS,
        )

    def test_run_generation_rejects_each_registered_spec_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = staged_root(Path(directory))
            original = runner.build_generation_spec(
                artifact,
                power_spec=POWER_SPEC,
                power_receipt=POWER_RECEIPT,
            )
            mutations = (
                ("seed", lambda value: value["invocation"].__setitem__("seed", 1)),
                (
                    "reference date",
                    lambda value: value["invocation"].__setitem__(
                        "reference_date", "2026-07-18"
                    ),
                ),
                (
                    "population",
                    lambda value: value["invocation"].__setitem__("population", 1),
                ),
                (
                    "output bound",
                    lambda value: value["output"].__setitem__("max_file_bytes", 1),
                ),
            )
            for label, mutate in mutations:
                with self.subTest(label=label):
                    spec = json.loads(json.dumps(original))
                    mutate(spec)
                    with self.assertRaisesRegex(ValueError, "does not match"):
                        runner.run_generation(
                            spec,
                            artifact_root=artifact,
                            power_spec=POWER_SPEC,
                            power_receipt=POWER_RECEIPT,
                        )


if __name__ == "__main__":
    unittest.main()
