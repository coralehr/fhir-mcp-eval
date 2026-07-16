from __future__ import annotations

import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path

import experiment_executor_deploy as deploy
import experiment_executor_install as install
import experiment_executor_service as service


RUNNER_PUBLIC_KEY = (
    "ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIOs43R3qv/9/ZBJeIT3hpuUgv7RYiusjUWsWR7PasmMy"
)
PYTHON_EXECUTABLE_PAYLOAD = b"synthetic pinned Python Mach-O payload"
PYTHON_TREE_ENTRIES = [
    {
        "path": "bin/python3.14",
        "sha256": hashlib.sha256(PYTHON_EXECUTABLE_PAYLOAD).hexdigest(),
        "bytes": len(PYTHON_EXECUTABLE_PAYLOAD),
        "mode": "0555",
        "owner": "root",
        "group": "wheel",
        "links": 1,
        "format": "macho",
        "dependencies": ["/usr/lib/libSystem.B.dylib"],
    }
]
PYTHON_TREE_RECEIPT = {
    "schema_version": install.PYTHON_TREE_SCHEMA_VERSION,
    "root": str(install.PRODUCTION_PYTHON_ROOT),
    "executable": str(service.PRODUCTION_PYTHON_PATH),
    "tree_sha256": install._python_tree_digest(PYTHON_TREE_ENTRIES),
    "files": 1,
    "bytes": len(PYTHON_EXECUTABLE_PAYLOAD),
    "version": install.PINNED_PYTHON_VERSION,
    "entries": PYTHON_TREE_ENTRIES,
}


class ExperimentExecutorInstallTests(unittest.TestCase):
    @staticmethod
    def _python_source(root: Path) -> Path:
        python_root = root / "python-source"
        executable = python_root / "bin/python3.14"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(PYTHON_EXECUTABLE_PAYLOAD)
        executable.chmod(0o555)
        return python_root

    def test_launcher_is_fixed_isolated_and_suppresses_bootstrap_stderr(self) -> None:
        launcher = install.render_launcher().decode("ascii")

        self.assertEqual(launcher, install.render_launcher().decode("ascii"))
        self.assertTrue(
            launcher.startswith(
                "#!/bin/sh\n"
                "exec 2>/dev/null\n"
                "set -efu\n"
                "umask 077\n"
                "ulimit -S -c 0 || exit 111\n"
                "ulimit -H -c 0 || exit 111\n"
            )
        )
        self.assertIn(
            f"cd '{service.PRODUCTION_BUNDLE_DIR}' || exit 111\n",
            launcher,
        )
        self.assertIn("exec /usr/bin/env -i \\\n", launcher)
        for key, value in service.PRODUCTION_ENVIRONMENT.items():
            self.assertIn(f"  {key}='{value}' \\\n", launcher)
        self.assertIn(
            f"  '{service.PRODUCTION_PYTHON_PATH}' -I -B -S \\\n",
            launcher,
        )
        self.assertTrue(
            launcher.endswith(
                f"  '{service.PRODUCTION_BOOTSTRAP_PATH}'\n"
            )
        )
        for hostile in (
            "SSH_ORIGINAL_COMMAND",
            "OPENAI_API_KEY",
            "PYTHONPATH",
            "NODE_OPTIONS",
            "HTTPS_PROXY",
        ):
            self.assertNotIn(hostile, launcher)

    def test_forced_key_is_localhost_only_restricted_and_caller_proof(self) -> None:
        line = install.render_authorized_key(
            RUNNER_PUBLIC_KEY + " ignored-comment"
        ).decode("ascii")

        self.assertEqual(
            line,
            (
                'from="127.0.0.1,::1",restrict,'
                f'command="{install.PRODUCTION_LAUNCHER_PATH}" '
                f"{RUNNER_PUBLIC_KEY} coralehr-experiment-rpc\n"
            ),
        )
        self.assertNotIn("ignored-comment", line)
        for hostile in (
            RUNNER_PUBLIC_KEY + "\nssh-ed25519 AAAA",
            'ssh-ed25519 AAAA command="/bin/sh"',
            "ssh-rsa AAAA",
            "not-a-key",
        ):
            with self.subTest(hostile=hostile[:40]), self.assertRaises(
                install.InstallProtocolError
            ):
                install.render_authorized_key(hostile)

    def test_install_package_is_deterministic_content_free_and_mode_bound(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python_source = self._python_source(root)
            first = root / "first"
            second = root / "second"
            first_manifest = install.build_install_package(
                source_root,
                first,
                python_source_root=python_source,
                runner_public_key=RUNNER_PUBLIC_KEY,
                python_tree_receipt=PYTHON_TREE_RECEIPT,
            )
            second_manifest = install.build_install_package(
                source_root,
                second,
                python_source_root=python_source,
                runner_public_key=RUNNER_PUBLIC_KEY,
                python_tree_receipt=PYTHON_TREE_RECEIPT,
            )

            self.assertEqual(first_manifest, second_manifest)
            self.assertEqual(
                install.validate_install_manifest(first_manifest), first_manifest
            )
            first_files = {
                path.relative_to(first): path.read_bytes()
                for path in first.rglob("*")
                if path.is_file()
            }
            second_files = {
                path.relative_to(second): path.read_bytes()
                for path in second.rglob("*")
                if path.is_file()
            }
            self.assertEqual(first_files, second_files)
            self.assertEqual(
                json.loads(first_files[Path("install-manifest.json")]),
                first_manifest,
            )
            self.assertEqual(
                set(first_manifest["code_subjects"]),
                {
                    "a11b_nightly_bootstrap",
                    "a11b_nightly_runner",
                    "a11b_grading",
                    "a11b_postprocess",
                    "anchor",
                    "bootstrap",
                    "codex_harness",
                    "driver",
                    "executor",
                    "installer",
                    "paired_stats",
                    "panel_grade",
                    "run_a11b_panel",
                    "run_lock",
                    "service",
                    "witness",
                },
            )
            self.assertEqual(first_manifest["executor_account"], "_coralexp")
            self.assertEqual(first_manifest["run_account"], "cory")
            self.assertEqual(first_manifest["model_calls"], 0)
            self.assertEqual(
                first_manifest["python_runtime"], PYTHON_TREE_RECEIPT
            )
            self.assertEqual(
                first_manifest["executor_principal"],
                {
                    "account": "_coralexp",
                    "admin": False,
                    "hidden": True,
                    "home": str(service.PRODUCTION_BUNDLE_DIR),
                    "password_authentication": False,
                    "shell": "/bin/sh",
                },
            )

            for relative, expected_mode in {
                "install-manifest.json": 0o400,
                "payload/run-experiment-executor-service": 0o500,
                "payload/authorized_keys.entry": 0o400,
                "payload/sshd_config.drop-in": 0o400,
                **{
                    subject["package_path"]: 0o400
                    for subject in first_manifest["code_subjects"].values()
                },
                "payload/python/bin/python3.14": 0o555,
            }.items():
                self.assertEqual(
                    stat.S_IMODE((first / relative).stat().st_mode),
                    expected_mode,
                )

            combined = b"\n".join(
                payload
                for path, payload in first_files.items()
                if not str(path).startswith("payload/code/")
            )
            for forbidden in (
                b"BEGIN OPENSSH PRIVATE KEY",
                b"credential-sentinel",
            ):
                self.assertNotIn(forbidden, combined)

            mutations = []
            wrong_path = json.loads(json.dumps(first_manifest))
            wrong_path["transport"]["launcher"]["path"] = "/tmp/root-write"
            mutations.append(wrong_path)
            extra_subject = json.loads(json.dumps(first_manifest))
            extra_subject["code_subjects"]["attacker"] = dict(
                next(iter(extra_subject["code_subjects"].values()))
            )
            mutations.append(extra_subject)
            wrong_principal = json.loads(json.dumps(first_manifest))
            wrong_principal["executor_principal"]["admin"] = True
            mutations.append(wrong_principal)
            for mutation in mutations:
                with self.assertRaises(install.InstallProtocolError):
                    install.validate_install_manifest(mutation)

    def test_package_refuses_overwrite_or_unexpected_source(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python_source = self._python_source(root)
            output = root / "package"
            output.mkdir()
            with self.assertRaises(FileExistsError):
                install.build_install_package(
                    source_root,
                    output,
                    python_source_root=python_source,
                    runner_public_key=RUNNER_PUBLIC_KEY,
                    python_tree_receipt=PYTHON_TREE_RECEIPT,
                )

            with self.assertRaises(install.InstallProtocolError):
                install.build_install_package(
                    root / "missing-source",
                    root / "other",
                    python_source_root=python_source,
                    runner_public_key=RUNNER_PUBLIC_KEY,
                    python_tree_receipt=PYTHON_TREE_RECEIPT,
                )

            for patch in (
                {"tree_sha256": "not-a-digest"},
                {"executable": "/opt/homebrew/bin/python3"},
                {"root": "/"},
                {"root": "relative/python"},
                {"version": "Python 3.13.9"},
                {"files": 0},
                {"bytes": 0},
                {
                    "entries": [
                        {
                            **PYTHON_TREE_ENTRIES[0],
                            "owner": "cory",
                        }
                    ]
                },
                {
                    "entries": [
                        {
                            **PYTHON_TREE_ENTRIES[0],
                            "dependencies": ["/opt/homebrew/lib/libPython.dylib"],
                        }
                    ]
                },
                *(
                    {
                        "entries": [
                            {
                                **PYTHON_TREE_ENTRIES[0],
                                "dependencies": [dependency],
                            }
                        ]
                    }
                    for dependency in (
                        "/usr/lib/../../../tmp/attacker.dylib",
                        "/System/Library/../../tmp/attacker.dylib",
                        str(install.PRODUCTION_PYTHON_ROOT)
                        + "/../attacker.dylib",
                        "/usr/lib//attacker.dylib",
                    )
                ),
            ):
                receipt = {**PYTHON_TREE_RECEIPT, **patch}
                if "entries" in patch:
                    entries = patch["entries"]
                    receipt["tree_sha256"] = install._python_tree_digest(entries)
                    receipt["files"] = len(entries)
                    receipt["bytes"] = sum(entry["bytes"] for entry in entries)
                with self.subTest(receipt_patch=patch), self.assertRaises(
                    install.InstallProtocolError
                ):
                    install.build_install_package(
                        source_root,
                        root / f"invalid-{len(list(root.iterdir()))}",
                        python_source_root=python_source,
                        runner_public_key=RUNNER_PUBLIC_KEY,
                        python_tree_receipt=receipt,
                    )

    def test_python_entry_paths_must_be_canonical(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        aliased = [
            dict(PYTHON_TREE_ENTRIES[0]),
            {
                "path": "lib/./python3.14/os.py",
                "sha256": "7" * 64,
                "bytes": 4096,
                "mode": "0444",
                "owner": "root",
                "group": "wheel",
                "links": 1,
                "format": "data",
                "dependencies": [],
            },
        ]
        receipt = {
            **PYTHON_TREE_RECEIPT,
            "tree_sha256": install._python_tree_digest(aliased),
            "files": len(aliased),
            "bytes": sum(int(entry["bytes"]) for entry in aliased),
            "entries": aliased,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python_source = self._python_source(root)
            with self.assertRaises(install.InstallProtocolError) as caught:
                install.build_install_package(
                    source_root,
                    root / "package",
                    python_source_root=python_source,
                    runner_public_key=RUNNER_PUBLIC_KEY,
                    python_tree_receipt=receipt,
                )
            self.assertEqual(str(caught.exception), "Python tree entry is invalid")

    def test_python_entry_paths_reject_apfs_case_or_nfc_collisions(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        for collider in ("Lib/python3.14/os.py", "lib/python3.14/OS.py"):
            data_entry = {
                "path": "lib/python3.14/os.py",
                "sha256": "7" * 64,
                "bytes": 4096,
                "mode": "0444",
                "owner": "root",
                "group": "wheel",
                "links": 1,
                "format": "data",
                "dependencies": [],
            }
            colliding = {**data_entry, "path": collider, "sha256": "8" * 64}
            entries = sorted(
                [dict(PYTHON_TREE_ENTRIES[0]), data_entry, colliding],
                key=lambda item: item["path"],
            )
            receipt = {
                **PYTHON_TREE_RECEIPT,
                "tree_sha256": install._python_tree_digest(entries),
                "files": len(entries),
                "bytes": sum(int(entry["bytes"]) for entry in entries),
                "entries": entries,
            }
            with tempfile.TemporaryDirectory() as directory, self.subTest(
                collider=collider
            ):
                root = Path(directory)
                python_source = self._python_source(root)
                with self.assertRaises(install.InstallProtocolError) as caught:
                    install.build_install_package(
                        source_root,
                        root / "package",
                        python_source_root=python_source,
                        runner_public_key=RUNNER_PUBLIC_KEY,
                        python_tree_receipt=receipt,
                    )
                self.assertEqual(
                    str(caught.exception), "Python tree entry path collides"
                )

    def test_python_entries_allow_zero_byte_files(self) -> None:
        source_root = Path(__file__).resolve().parents[1]
        entries = [
            dict(PYTHON_TREE_ENTRIES[0]),
            {
                "path": "lib/python3.14/__init__.py",
                "sha256": hashlib.sha256(b"").hexdigest(),
                "bytes": 0,
                "mode": "0444",
                "owner": "root",
                "group": "wheel",
                "links": 1,
                "format": "data",
                "dependencies": [],
            },
        ]
        receipt = {
            **PYTHON_TREE_RECEIPT,
            "tree_sha256": install._python_tree_digest(entries),
            "files": len(entries),
            "bytes": sum(int(entry["bytes"]) for entry in entries),
            "entries": entries,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python_source = self._python_source(root)
            empty_module = python_source / "lib/python3.14/__init__.py"
            empty_module.parent.mkdir(parents=True)
            empty_module.write_bytes(b"")
            empty_module.chmod(0o444)
            manifest = install.build_install_package(
                source_root,
                root / "package",
                python_source_root=python_source,
                runner_public_key=RUNNER_PUBLIC_KEY,
                python_tree_receipt=receipt,
            )
        self.assertEqual(manifest["python_runtime"]["files"], 2)
        self.assertEqual(manifest["python_runtime"]["entries"][1]["bytes"], 0)

    def test_sshd_drop_in_is_exact_public_key_only_single_session_policy(self) -> None:
        config = install.render_sshd_drop_in().decode("ascii")
        self.assertEqual(config, install.render_sshd_drop_in().decode("ascii"))
        self.assertTrue(config.startswith("Match User _coralexp\n"))
        for line in (
            "    AuthenticationMethods publickey\n",
            "    PasswordAuthentication no\n",
            "    KbdInteractiveAuthentication no\n",
            "    PubkeyAuthentication yes\n",
            f"    AuthorizedKeysFile {install.PRODUCTION_AUTHORIZED_KEYS_PATH}\n",
            f"    ForceCommand {install.PRODUCTION_LAUNCHER_PATH}\n",
            "    DisableForwarding yes\n",
            "    PermitTTY no\n",
            "    PermitTunnel no\n",
            "    PermitUserRC no\n",
            "    MaxSessions 1\n",
        ):
            self.assertIn(line, config)
        self.assertTrue(config.endswith("Match all\n"))

    def test_sshd_drop_in_preflight_supplies_a_sealed_host_key(self) -> None:
        package_root = Path("/reviewed/install-package")
        bundle_root = Path("/reviewed/controller-bundle")

        self.assertEqual(
            deploy._sshd_drop_in_check_command(package_root, bundle_root),
            [
                "/usr/sbin/sshd",
                "-t",
                "-f",
                str(package_root / "payload/sshd_config.drop-in"),
                "-h",
                str(bundle_root / "witness_ed25519"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
