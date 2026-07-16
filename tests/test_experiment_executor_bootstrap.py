from __future__ import annotations

import os
import resource
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import experiment_executor_bootstrap as bootstrap


class ExperimentExecutorBootstrapTests(unittest.TestCase):
    def _materialize_sealed_tree(
        self, code: Path, *, service_body: str, service_mode: int = 0o400
    ) -> Path:
        """Write a full sealed code tree the bootstrap gate will accept.

        Every module in _SEALED_CODE_FILENAMES must exist, be owned by the
        (test) uid the copied bootstrap is told to require, and be immutable.
        The fake service imports only fixed_dependency + json, so the five
        sibling modules are inert stubs that merely have to pass the file gate.
        """
        code.mkdir(mode=0o700)
        (code / "fixed_dependency.py").write_text(
            "VALUE = 'fixed-import-ok'\n", encoding="utf-8"
        )
        (code / "json.py").write_text(
            "raise RuntimeError('stdlib shadowed')\n", encoding="utf-8"
        )
        service = code / "experiment_executor_service.py"
        service.write_text(service_body, encoding="utf-8")

        bootstrap_path = code / "experiment_executor_bootstrap.py"
        source = Path(bootstrap.__file__).read_text(encoding="utf-8")
        for old, new in {
            str(bootstrap.BUNDLE_DIR): str(code.parent / "bundle"),
            str(bootstrap.CODE_DIR): str(code),
        }.items():
            source = source.replace(old, new)
        source = source.replace(
            'PYTHON_PATH = CODE_DIR / "python/bin/python3.14"',
            f"PYTHON_PATH = Path({str(Path(sys.executable).resolve())!r})",
        )
        # The real gate requires strictly-root-owned files; a test cannot create
        # them, so the copied bootstrap is told to require the test uid instead.
        source = source.replace(
            "_REQUIRED_FILE_UID = 0", f"_REQUIRED_FILE_UID = {os.getuid()}"
        )
        bootstrap_path.write_text(source, encoding="utf-8")

        for filename in bootstrap._SEALED_CODE_FILENAMES:
            target = code / filename
            if not target.exists():
                target.write_text("# sealed sibling stub\n", encoding="utf-8")
        for filename in bootstrap._SEALED_CODE_FILENAMES:
            (code / filename).chmod(0o400)
        service.chmod(service_mode)
        return bootstrap_path

    def _run_bootstrap(self, bundle: Path, bootstrap_path: Path, tmpdir: Path):
        return subprocess.run(
            [
                str(Path(sys.executable).resolve()),
                "-I",
                "-B",
                "-S",
                str(bootstrap_path),
            ],
            cwd=bundle,
            env={
                "HOME": str(bundle),
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "TMPDIR": str(tmpdir),
            },
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            preexec_fn=lambda: (
                os.umask(0o077),
                resource.setrlimit(resource.RLIMIT_CORE, (0, 0)),
            ),
        )

    def test_exact_isolated_fresh_process_imports_only_fixed_code_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle = root / "bundle"
            tmpdir = bundle / "scratch/service-tmp"
            tmpdir.mkdir(parents=True, mode=0o700)
            bootstrap_path = self._materialize_sealed_tree(
                root / "code",
                service_body=(
                    "import fixed_dependency, json\n"
                    "assert '/json/' in json.__file__\n"
                    "print(fixed_dependency.VALUE, flush=True)\n"
                    "raise SystemExit(0)\n"
                ),
            )

            process = self._run_bootstrap(bundle, bootstrap_path, tmpdir)

            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(process.stdout, "fixed-import-ok\n")
            self.assertEqual(process.stderr, "")
            self.assertFalse(any(root.rglob("*.pyc")))
            self.assertFalse(any(root.rglob("__pycache__")))

    def test_writable_sealed_file_is_rejected_before_the_service_runs(self) -> None:
        # Wiring proof: a run-account-writable service file must be rejected by
        # the gate inside _prepare_service, so run_path never executes it.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle = root / "bundle"
            tmpdir = bundle / "scratch/service-tmp"
            tmpdir.mkdir(parents=True, mode=0o700)
            bootstrap_path = self._materialize_sealed_tree(
                root / "code",
                service_body="print('SHOULD-NOT-RUN', flush=True)\n",
                service_mode=0o644,
            )

            process = self._run_bootstrap(bundle, bootstrap_path, tmpdir)

            self.assertNotEqual(process.returncode, 0)
            self.assertNotIn("SHOULD-NOT-RUN", process.stdout)

    def test_writable_sibling_module_is_rejected_before_the_service_runs(self) -> None:
        # The service imports its siblings at load time, so an individually
        # run-account-writable sibling would execute hostile code at import.
        # The gate must reject it even though the service file itself is sealed.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle = root / "bundle"
            tmpdir = bundle / "scratch/service-tmp"
            tmpdir.mkdir(parents=True, mode=0o700)
            code = root / "code"
            bootstrap_path = self._materialize_sealed_tree(
                code,
                service_body=(
                    "import fixed_dependency, json\n"
                    "print(fixed_dependency.VALUE, flush=True)\n"
                    "raise SystemExit(0)\n"
                ),
            )
            self.assertIn("codex_harness.py", bootstrap._SEALED_CODE_FILENAMES)
            (code / "codex_harness.py").chmod(0o644)

            process = self._run_bootstrap(bundle, bootstrap_path, tmpdir)

            self.assertNotEqual(process.returncode, 0)
            self.assertNotIn("fixed-import-ok", process.stdout)

    def test_prepare_rejects_nonisolated_ambient_process(self) -> None:
        with self.assertRaises(RuntimeError):
            bootstrap._prepare_service()

    def test_sealed_file_gate_rejects_writable_or_symlinked_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            bootstrap, "_REQUIRED_FILE_UID", os.getuid()
        ):
            code = Path(directory).resolve() / "code"
            code.mkdir(mode=0o700)
            immutable = code / "experiment_executor_service.py"
            immutable.write_text("x = 1\n", encoding="utf-8")
            immutable.chmod(0o400)
            bootstrap._require_immutable_sealed_file(immutable)

            writable = code / "writable.py"
            writable.write_text("x = 1\n", encoding="utf-8")
            writable.chmod(0o644)
            with self.assertRaises(RuntimeError):
                bootstrap._require_immutable_sealed_file(writable)

            link = code / "linked.py"
            link.symlink_to(immutable)
            with self.assertRaises(RuntimeError):
                bootstrap._require_immutable_sealed_file(link)

    def test_sealed_file_gate_rejects_foreign_owner(self) -> None:
        # With the production strict-root requirement, a file owned by the
        # running (non-root) account is rejected.
        with tempfile.TemporaryDirectory() as directory:
            code = Path(directory).resolve() / "code"
            code.mkdir(mode=0o700)
            sealed = code / "experiment_executor_service.py"
            sealed.write_text("x = 1\n", encoding="utf-8")
            sealed.chmod(0o400)
            self.assertNotEqual(os.getuid(), bootstrap._REQUIRED_FILE_UID)
            with self.assertRaises(RuntimeError):
                bootstrap._require_immutable_sealed_file(sealed)

    def test_ancestor_gate_rejects_group_or_other_writable_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            safe = Path(directory).resolve() / "code"
            safe.mkdir(mode=0o700)
            try:
                bootstrap._require_safe_code_ancestors(safe)
            except RuntimeError as exc:
                self.skipTest(f"temp ancestor chain is not root-safe here: {exc}")

            unsafe = Path(directory).resolve() / "loose"
            unsafe.mkdir(mode=0o700)
            unsafe.chmod(0o777)
            with self.assertRaises(RuntimeError):
                bootstrap._require_safe_code_ancestors(unsafe)


if __name__ == "__main__":
    unittest.main()
