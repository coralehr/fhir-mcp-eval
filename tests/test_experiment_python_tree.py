from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import experiment_executor_install as install
from experiment_python_tree import build_python_tree_receipt, stage_python_tree


class ExperimentPythonTreeTests(unittest.TestCase):
    def test_receipt_is_deterministic_and_maps_only_regular_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "python"
            (root / "bin").mkdir(parents=True)
            executable = root / "bin" / "python3.14"
            executable.write_bytes(b"\xcf\xfa\xed\xfe" + b"native")
            executable.chmod(0o755)
            (root / "lib").mkdir()
            (root / "lib" / "module.py").write_text("VALUE = 1\n")
            (root / "python3").symlink_to("bin/python3.14")

            receipt = build_python_tree_receipt(
                root,
                dependency_reader=lambda _path: ["/usr/lib/libSystem.B.dylib"],
            )
            replay = build_python_tree_receipt(
                root,
                dependency_reader=lambda _path: ["/usr/lib/libSystem.B.dylib"],
            )

        self.assertEqual(receipt, replay)
        self.assertEqual(receipt["version"], install.PINNED_PYTHON_VERSION)
        self.assertEqual(receipt["files"], 2)
        self.assertEqual(receipt["executable"], str(install.service.PRODUCTION_PYTHON_PATH))
        self.assertEqual(
            [entry["path"] for entry in receipt["entries"]],
            ["bin/python3.14", "lib/module.py"],
        )
        self.assertEqual(receipt["entries"][0]["format"], "macho")
        self.assertEqual(receipt["entries"][0]["mode"], "0555")
        self.assertEqual(receipt["entries"][1]["format"], "data")
        self.assertEqual(receipt["entries"][1]["mode"], "0444")
        install._python_runtime_receipt(receipt)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            (source / "bin").mkdir(parents=True)
            (source / "bin/python3.14").write_bytes(b"\xcf\xfa\xed\xfe" + b"native")
            (source / "lib").mkdir()
            (source / "lib/module.py").write_text("VALUE = 1\n")
            destination = Path(directory) / "staged"
            stage_python_tree(source, destination, receipt)
            self.assertEqual(
                (destination / "bin/python3.14").read_bytes(),
                b"\xcf\xfa\xed\xfe" + b"native",
            )
            self.assertFalse((destination / "bin/python3.14").stat().st_mode & 0o022)

    def test_external_or_relative_macho_dependency_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "python"
            (root / "bin").mkdir(parents=True)
            executable = root / "bin" / "python3.14"
            executable.write_bytes(b"\xcf\xfa\xed\xfe" + b"native")
            executable.chmod(0o755)
            for dependency in ("@rpath/libpython.dylib", "/opt/homebrew/lib/libx.dylib"):
                with self.subTest(dependency=dependency):
                    with self.assertRaisesRegex(ValueError, "dependency"):
                        build_python_tree_receipt(
                            root,
                            dependency_reader=lambda _path, item=dependency: [item],
                        )


if __name__ == "__main__":
    unittest.main()
