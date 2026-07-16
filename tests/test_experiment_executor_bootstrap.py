from __future__ import annotations

import os
import resource
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import experiment_executor_bootstrap as bootstrap


class ExperimentExecutorBootstrapTests(unittest.TestCase):
    def test_exact_isolated_fresh_process_imports_only_fixed_code_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle = root / "bundle"
            code = root / "code"
            tmpdir = bundle / "scratch/service-tmp"
            code.mkdir(mode=0o700)
            tmpdir.mkdir(parents=True, mode=0o700)
            dependency = code / "fixed_dependency.py"
            dependency.write_text("VALUE = 'fixed-import-ok'\n", encoding="utf-8")
            (code / "json.py").write_text(
                "raise RuntimeError('stdlib shadowed')\n",
                encoding="utf-8",
            )
            service = code / "experiment_executor_service.py"
            service.write_text(
                "import fixed_dependency, json\n"
                "assert '/json/' in json.__file__\n"
                "print(fixed_dependency.VALUE, flush=True)\n"
                "raise SystemExit(0)\n",
                encoding="utf-8",
            )
            bootstrap_path = code / "experiment_executor_bootstrap.py"
            source = Path(bootstrap.__file__).read_text(encoding="utf-8")
            replacements = {
                str(bootstrap.BUNDLE_DIR): str(bundle),
                str(bootstrap.CODE_DIR): str(code),
            }
            for old, new in replacements.items():
                source = source.replace(old, new)
            source = source.replace(
                'PYTHON_PATH = CODE_DIR / "python/bin/python3.14"',
                f"PYTHON_PATH = Path({str(Path(sys.executable).resolve())!r})",
            )
            bootstrap_path.write_text(source, encoding="utf-8")
            environment = {
                "HOME": str(bundle),
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "TMPDIR": str(tmpdir),
            }

            process = subprocess.run(
                [
                    str(Path(sys.executable).resolve()),
                    "-I",
                    "-B",
                    "-S",
                    str(bootstrap_path),
                ],
                cwd=bundle,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                preexec_fn=lambda: (
                    os.umask(0o077),
                    resource.setrlimit(resource.RLIMIT_CORE, (0, 0)),
                ),
            )

            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(process.stdout, "fixed-import-ok\n")
            self.assertEqual(process.stderr, "")
            self.assertFalse(any(root.rglob("*.pyc")))
            self.assertFalse(any(root.rglob("__pycache__")))

    def test_prepare_rejects_nonisolated_ambient_process(self) -> None:
        with self.assertRaises(RuntimeError):
            bootstrap._prepare_service()


if __name__ == "__main__":
    unittest.main()
