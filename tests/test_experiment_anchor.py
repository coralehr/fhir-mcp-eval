from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import experiment_anchor


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_controller(root: Path) -> Path:
    snapshots = {
        name: {"sha256": sha(name.encode()), "bytes": len(name)}
        for name in (
            "preregistration",
            "packet_v",
            "packet_t",
            "packet_e",
            "schema",
            "a11_grading",
            "run_a11_panel",
            "panel_grade",
        )
    }
    manifest = {
        "kind": "a11_interleaved_controller_manifest",
        "schema_version": "a11-controller-v3",
        "experiment_profile": "a11b-causal-isolation-v1",
        "execution": {
            "model": "gpt-test",
            "reasoning_effort": "high",
            "timeout_seconds": 600,
            "codex": {
                "path": "/sealed/codex.js",
                "sha256": "1" * 64,
                "bytes": 10,
                "version": "codex-cli 1.2.3",
                "native": {
                    "path": "/sealed/native/codex",
                    "sha256": "2" * 64,
                    "bytes": 20,
                },
            },
        },
        "grading": {
            "panel": {
                "model": "judge-test",
                "reasoning_effort": "high",
                "votes": 3,
                "batch_size": 20,
                "timeout_seconds": 600,
            }
        },
        "snapshots": snapshots,
    }
    path = root / "manifest.json"
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def trusted_executor_binding() -> dict[str, object]:
    receipt = lambda path, digest, size: {  # noqa: E731
        "path": path,
        "sha256": digest,
        "bytes": size,
    }
    schedule = [
        {
            "phase": "answer",
            "schedule_index": 0,
            "call_commitment": "3" * 64,
            "max_attempts": 1,
        },
        {
            "phase": "panel",
            "schedule_index": 0,
            "call_commitment": "4" * 64,
            "max_attempts": 1,
        },
    ]
    return {
        "bundle_commitment": "1" * 64,
        "bundle_schema_version": "experiment-executor-service-bundle-v1",
        "service_protocol_version": "experiment-executor-service-v1",
        "run_id": "2" * 64,
        "witness": {
            "identity": "a11b-witness",
            "public_key": "ssh-ed25519 AAAATEST",
            "key_id": "sha256:" + "5" * 64,
            "schedule": schedule,
        },
        "runtime": {
            **receipt("/sealed/native/codex", "6" * 64, 100),
            "version": "codex-cli 0.144.1",
        },
        "sandbox": {
            **receipt("/usr/bin/sandbox-exec", "7" * 64, 200),
            "profile": "(version 1)(allow default)(deny process-fork)",
        },
        "executables": {
            "python": receipt("/sealed/python", "8" * 64, 300),
            "ssh_keygen": receipt("/usr/bin/ssh-keygen", "9" * 64, 400),
        },
        "code_subjects": [
            {"name": name, "sha256": digest * 64, "bytes": index + 1}
            for index, (name, digest) in enumerate(
                (
                    ("anchor", "a"),
                    ("codex_harness", "b"),
                    ("driver", "c"),
                    ("executor", "d"),
                    ("service", "e"),
                    ("witness", "f"),
                )
            )
        ],
        "model_configuration": {
            "answer": {
                "model": "gpt-test",
                "reasoning_effort": "high",
                "timeout_seconds": 600,
            },
            "panel": {
                "model": "judge-test",
                "reasoning_effort": "high",
                "votes": 3,
                "batch_size": 20,
                "timeout_seconds": 600,
            },
        },
        "anchor_verifier": {
            "algorithm": "ssh-ed25519",
            "identity": "coralehr-anchor-checker-2026-07",
            "namespace": experiment_anchor.SIGNED_ANCHOR_NAMESPACE,
            "public_key": (
                "ssh-ed25519 "
                "AAAAC3NzaC1lZDI1NTE5AAAAIOs43R3qv/9/ZBJeIT3hpuUgv7RYiusjUWsWR7PasmMy"
            ),
            "key_id": (
                "sha256:3ae9cbfd77e5bc24ad2914ea0fa2cb6a473ccdf9f70cc914c456c86371f2bd9d"
            ),
        },
    }


def write_v4_controller(root: Path) -> Path:
    path = write_controller(root)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "a11-controller-v4"
    binding = trusted_executor_binding()
    manifest["execution"]["trusted_executor"] = binding
    manifest["execution"]["codex"]["native"] = {
        key: binding["runtime"][key] for key in ("path", "sha256", "bytes")
    }
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def verified_remote_fetch(
    expected: bytes,
    commit: str,
    *,
    author: str = "aanishs",
    author_id: int = 1001,
    merger: str = "aanishs",
    merger_id: int = 1001,
    reviewer: str = "AJ112103",
    reviewer_id: int = 143727238,
    reviewed_path: str = "anchors/a11b/anchor-request.json",
    reviewed_head_bytes: bytes | None = None,
):
    head = "d" * 40
    reviewed_head_bytes = (
        expected if reviewed_head_bytes is None else reviewed_head_bytes
    )

    def fetch(request_url: str, accept: str) -> bytes:
        if "/contents/" in request_url:
            if accept != "application/vnd.github.raw+json":
                raise AssertionError(accept)
            if f"ref={head}" in request_url:
                return reviewed_head_bytes
            return expected
        if request_url.endswith(f"/commits/{commit}"):
            return json.dumps(
                {
                    "sha": commit,
                    "commit": {
                        "verification": {
                            "verified": True,
                            "reason": "valid",
                            "verified_at": "2026-07-15T14:15:36Z",
                        }
                    },
                }
            ).encode()
        if f"/commits/{commit}/pulls?" in request_url:
            return json.dumps(
                [
                    {
                        "number": 49,
                        "html_url": "https://github.com/coralehr/fhir-mcp-eval/pull/49",
                        "state": "closed",
                        "merged_at": "2026-07-15T14:15:35Z",
                        "merge_commit_sha": commit,
                        "base": {"ref": "main"},
                        "head": {"sha": head},
                        "user": {"login": author, "id": author_id},
                    }
                ]
            ).encode()
        if request_url.endswith("/pulls/49"):
            return json.dumps(
                {
                    "number": 49,
                    "html_url": "https://github.com/coralehr/fhir-mcp-eval/pull/49",
                    "state": "closed",
                    "merged_at": "2026-07-15T14:15:35Z",
                    "merge_commit_sha": commit,
                    "base": {"ref": "main"},
                    "head": {"sha": head},
                    "user": {"login": author, "id": author_id},
                    "merged_by": {"login": merger, "id": merger_id},
                }
            ).encode()
        if "/pulls/49/files?" in request_url:
            return json.dumps(
                [
                    {
                        "filename": reviewed_path,
                        "status": "added",
                    }
                ]
            ).encode()
        if "/pulls/49/reviews?" in request_url:
            return json.dumps(
                [
                    {
                        "id": 9001,
                        "state": "APPROVED",
                        "commit_id": head,
                        "submitted_at": "2026-07-15T14:10:00Z",
                        "author_association": "MEMBER",
                        "user": {
                            "login": reviewer,
                            "id": reviewer_id,
                            "type": "User",
                        },
                    }
                ]
            ).encode()
        raise AssertionError(request_url)

    return fetch


class ExperimentAnchorTests(unittest.TestCase):
    def _checker(
        self, root: Path, name: str
    ) -> tuple[Path, dict[str, str]]:
        private_key = root / name
        subprocess.run(
            [
                str(experiment_anchor.SSH_KEYGEN_PATH),
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                name,
                "-f",
                str(private_key),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        public_fields = subprocess.run(
            [
                str(experiment_anchor.SSH_KEYGEN_PATH),
                "-y",
                "-f",
                str(private_key),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().split()
        public_key = " ".join(public_fields[:2])
        return private_key, {
            "algorithm": "ssh-ed25519",
            "identity": name,
            "namespace": experiment_anchor.SIGNED_ANCHOR_NAMESPACE,
            "public_key": public_key,
            "key_id": "sha256:"
            + sha((public_key + "\n").encode("ascii")),
        }

    def test_v4_request_binds_exact_trusted_executor_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = write_v4_controller(Path(directory))

            request = experiment_anchor.build_anchor_request(controller)

            self.assertEqual(
                request["schema_version"], "experiment-external-anchor-v2"
            )
            self.assertEqual(
                request["trusted_executor"], trusted_executor_binding()
            )
            self.assertEqual(
                request["controller"]["schema_version"], "a11-controller-v4"
            )

    def test_v4_trusted_executor_binding_rejects_noncanonical_or_unsafe_fields(
        self,
    ) -> None:
        mutations = {
            "schedule_gap": lambda value: value["witness"]["schedule"][1].update(
                {"schedule_index": 1}
            ),
            "relative_runtime": lambda value: value["runtime"].update(
                {"path": "relative/codex"}
            ),
            "sandbox_profile": lambda value: value["sandbox"].update(
                {"profile": "(allow default)"}
            ),
            "code_order": lambda value: value["code_subjects"].reverse(),
            "model_timeout": lambda value: value["model_configuration"][
                "answer"
            ].update({"timeout_seconds": 0}),
            "extra_field": lambda value: value.update({"prompt": "must-not-publish"}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                controller = write_v4_controller(Path(directory))
                manifest = json.loads(controller.read_text(encoding="utf-8"))
                mutate(manifest["execution"]["trusted_executor"])
                controller.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

                with self.assertRaises(ValueError):
                    experiment_anchor.build_anchor_request(controller)

    def test_v4_rejects_outer_model_or_runtime_that_differs_from_service(self) -> None:
        mutations = {
            "answer_model": lambda value: value["execution"].update(
                {"model": "other-model"}
            ),
            "answer_reasoning": lambda value: value["execution"].update(
                {"reasoning_effort": "medium"}
            ),
            "answer_timeout": lambda value: value["execution"].update(
                {"timeout_seconds": 601}
            ),
            "panel_model": lambda value: value["grading"]["panel"].update(
                {"model": "other-judge"}
            ),
            "panel_reasoning": lambda value: value["grading"]["panel"].update(
                {"reasoning_effort": "medium"}
            ),
            "panel_votes": lambda value: value["grading"]["panel"].update(
                {"votes": 5}
            ),
            "panel_batch": lambda value: value["grading"]["panel"].update(
                {"batch_size": 21}
            ),
            "panel_timeout": lambda value: value["grading"]["panel"].update(
                {"timeout_seconds": 601}
            ),
            "native_path": lambda value: value["execution"]["codex"][
                "native"
            ].update({"path": "/sealed/native/other"}),
            "native_digest": lambda value: value["execution"]["codex"][
                "native"
            ].update({"sha256": "0" * 64}),
            "native_bytes": lambda value: value["execution"]["codex"][
                "native"
            ].update({"bytes": 101}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                controller = write_v4_controller(Path(directory))
                manifest = json.loads(controller.read_text(encoding="utf-8"))
                mutate(manifest)
                controller.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(ValueError):
                    experiment_anchor.build_anchor_request(controller)

    def test_v4_service_anchor_uses_existing_exact_head_approval_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = write_v4_controller(Path(directory))
            expected = experiment_anchor.canonical_json_bytes(
                experiment_anchor.build_anchor_request(controller)
            )
            commit = "c" * 40
            url = (
                "https://api.github.com/repos/coralehr/fhir-mcp-eval/contents/"
                f"anchors/a11b/anchor-request.json?ref={commit}"
            )

            receipt = experiment_anchor.verify_external_anchor(
                controller,
                url,
                expected_controller_sha256=sha(controller.read_bytes()),
                fetch_bytes=verified_remote_fetch(expected, commit),
            )

            self.assertEqual(receipt["external_commit_sha"], commit)
            self.assertEqual(
                receipt["anchor_request_sha256"], sha(expected)
            )
            self.assertEqual(receipt["independent_approvers"], ["AJ112103"])

    def test_signed_anchor_receipt_is_offline_verifiable_and_tamper_evident(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = write_v4_controller(root)
            expected = experiment_anchor.canonical_json_bytes(
                experiment_anchor.build_anchor_request(controller)
            )
            commit = "d" * 40
            url = (
                "https://api.github.com/repos/coralehr/fhir-mcp-eval/contents/"
                f"anchors/a11b/anchor-request.json?ref={commit}"
            )
            receipt = experiment_anchor.verify_external_anchor(
                controller,
                url,
                expected_controller_sha256=sha(controller.read_bytes()),
                fetch_bytes=verified_remote_fetch(expected, commit),
            )
            private_key, verifier = self._checker(root, "independent-checker")
            signed = experiment_anchor.sign_external_anchor_verification(
                receipt,
                private_key_path=private_key,
                verifier=verifier,
            )
            signed_bytes = experiment_anchor.canonical_json_bytes(signed)

            verified = experiment_anchor.verify_signed_external_anchor_receipt(
                controller,
                url,
                signed_bytes,
                expected_controller_sha256=sha(controller.read_bytes()),
                expected_verifier=verifier,
            )
            self.assertEqual(verified, receipt)

            tampered = json.loads(signed_bytes)
            tampered["body"]["independent_approvers"] = ["Arhaan2104"]
            tampered["body"]["independent_approver_ids"] = [143709176]
            tampered["body_sha256"] = sha(
                experiment_anchor.canonical_json_bytes(tampered["body"])
            )
            with self.assertRaises(ValueError):
                experiment_anchor.verify_signed_external_anchor_receipt(
                    controller,
                    url,
                    experiment_anchor.canonical_json_bytes(tampered),
                    expected_controller_sha256=sha(controller.read_bytes()),
                    expected_verifier=verifier,
                )

    def test_forged_or_attacker_signed_anchor_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = write_v4_controller(root)
            expected = experiment_anchor.canonical_json_bytes(
                experiment_anchor.build_anchor_request(controller)
            )
            commit = "e" * 40
            url = (
                "https://api.github.com/repos/coralehr/fhir-mcp-eval/contents/"
                f"anchors/a11b/anchor-request.json?ref={commit}"
            )
            receipt = experiment_anchor.verify_external_anchor(
                controller,
                url,
                expected_controller_sha256=sha(controller.read_bytes()),
                fetch_bytes=verified_remote_fetch(expected, commit),
            )
            trusted_key, trusted_verifier = self._checker(root, "trusted-checker")
            attacker_key, attacker_verifier = self._checker(root, "attacker-checker")
            del trusted_key

            with self.assertRaises(ValueError):
                experiment_anchor.verify_signed_external_anchor_receipt(
                    controller,
                    url,
                    experiment_anchor.canonical_json_bytes(receipt),
                    expected_controller_sha256=sha(controller.read_bytes()),
                    expected_verifier=trusted_verifier,
                )

            attacker_signed = experiment_anchor.sign_external_anchor_verification(
                receipt,
                private_key_path=attacker_key,
                verifier=attacker_verifier,
            )
            with self.assertRaises(ValueError):
                experiment_anchor.verify_signed_external_anchor_receipt(
                    controller,
                    url,
                    experiment_anchor.canonical_json_bytes(attacker_signed),
                    expected_controller_sha256=sha(controller.read_bytes()),
                    expected_verifier=trusted_verifier,
                )

    def test_controller_manifest_rejects_symlink_and_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = write_controller(root)
            symlink = root / "controller-link.json"
            symlink.symlink_to(controller)
            with self.assertRaisesRegex(ValueError, "regular file"):
                experiment_anchor.build_anchor_request(symlink)

            duplicate = root / "duplicate.json"
            duplicate.write_text(
                '{"schema_version":"a11-controller-v3",'
                '"schema_version":"a11-controller-v3"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                experiment_anchor.build_anchor_request(duplicate)

    def test_request_binds_controller_inputs_native_runtime_and_graders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = write_controller(Path(directory))

            request = experiment_anchor.build_anchor_request(controller)

            self.assertEqual(
                request["controller"],
                {
                    "kind": "a11_interleaved_controller_manifest",
                    "schema_version": "a11-controller-v3",
                    "sha256": sha(controller.read_bytes()),
                    "bytes": controller.stat().st_size,
                },
            )
            self.assertEqual(
                set(request["subjects"]),
                {
                    "preregistration",
                    "packet_v",
                    "packet_t",
                    "packet_e",
                    "answer_schema",
                    "native_codex",
                    "a11_grading",
                    "run_a11_panel",
                    "panel_grade",
                },
            )
            self.assertEqual(
                request["subjects"]["native_codex"],
                {"sha256": "2" * 64, "bytes": 20},
            )
            self.assertEqual(
                request["model_configuration"],
                {
                    "answer": {
                        "model": "gpt-test",
                        "reasoning_effort": "high",
                        "timeout_seconds": 600,
                    },
                    "panel": {
                        "model": "judge-test",
                        "reasoning_effort": "high",
                        "votes": 3,
                        "batch_size": 20,
                        "timeout_seconds": 600,
                    },
                },
            )

    def test_write_request_is_exclusive_canonical_and_hash_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = write_controller(root)
            output = root / "anchor-request.json"

            receipt = experiment_anchor.write_anchor_request(controller, output)

            expected = experiment_anchor.canonical_json_bytes(
                experiment_anchor.build_anchor_request(controller)
            )
            self.assertEqual(output.read_bytes(), expected)
            self.assertEqual(
                output.with_suffix(".sha256").read_text(encoding="ascii"),
                sha(expected) + "\n",
            )
            self.assertEqual(
                receipt,
                {
                    "path": str(output.resolve()),
                    "sha256": sha(expected),
                    "bytes": len(expected),
                },
            )
            with self.assertRaises(FileExistsError):
                experiment_anchor.write_anchor_request(controller, output)

            output.chmod(0o644)
            output.write_bytes(expected + b" ")
            with self.assertRaisesRegex(ValueError, "local anchor request changed"):
                experiment_anchor.verify_local_anchor_request(controller, output)

    def test_anchor_request_late_write_failure_cleans_both_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = write_controller(root)
            output = root / "anchor-request.json"
            with (
                mock.patch.object(
                    experiment_anchor.os,
                    "fsync",
                    side_effect=[None, OSError("synthetic sidecar fsync failure")],
                ),
                self.assertRaisesRegex(OSError, "sidecar fsync"),
            ):
                experiment_anchor.write_anchor_request(controller, output)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".sha256").exists())

    def test_anchor_url_requires_trusted_repo_anchor_path_and_full_commit(self) -> None:
        commit = "a" * 40
        url = (
            "https://api.github.com/repos/coralehr/fhir-mcp-eval/contents/"
            f"anchors/a11b/anchor-request.json?ref={commit}"
        )

        locator = experiment_anchor.parse_github_anchor_url(url)

        self.assertEqual(locator.commit_sha, commit)
        self.assertEqual(locator.path, "anchors/a11b/anchor-request.json")
        self.assertEqual(
            locator.commit_url,
            f"https://api.github.com/repos/coralehr/fhir-mcp-eval/commits/{commit}",
        )
        for invalid in (
            url.replace(commit, "main"),
            url.replace("coralehr/fhir-mcp-eval", "attacker/fhir-mcp-eval"),
            url.replace("anchors/a11b", "docs/a11b"),
            url + "&extra=true",
            url.replace("https://", "http://"),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    experiment_anchor.parse_github_anchor_url(invalid)

    def test_verify_requires_exact_request_bytes_and_verified_github_commit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = write_controller(Path(directory))
            expected = experiment_anchor.canonical_json_bytes(
                experiment_anchor.build_anchor_request(controller)
            )
            commit = "b" * 40
            url = (
                "https://api.github.com/repos/coralehr/fhir-mcp-eval/contents/"
                f"anchors/a11b/anchor-request.json?ref={commit}"
            )

            fetch_exact = verified_remote_fetch(expected, commit)

            receipt = experiment_anchor.verify_external_anchor(
                controller,
                url,
                expected_controller_sha256=sha(controller.read_bytes()),
                fetch_bytes=fetch_exact,
            )

            self.assertEqual(receipt["external_commit_sha"], commit)
            self.assertEqual(receipt["anchor_request_sha256"], sha(expected))
            self.assertEqual(
                receipt["github_signature_verified_at"], "2026-07-15T14:15:36Z"
            )
            self.assertEqual(receipt["anchor_pr_number"], 49)
            self.assertEqual(receipt["independent_approvers"], ["AJ112103"])
            self.assertEqual(receipt["independent_approver_ids"], [143727238])
            self.assertEqual(receipt["anchor_path"], "anchors/a11b/anchor-request.json")
            self.assertEqual(receipt["anchor_pr_file_status"], "added")

            def fetch_changed(request_url: str, accept: str) -> bytes:
                if "/contents/" in request_url:
                    return expected + b" "
                return fetch_exact(request_url, accept)

            with self.assertRaisesRegex(ValueError, "bytes differ"):
                experiment_anchor.verify_external_anchor(
                    controller,
                    url,
                    expected_controller_sha256=sha(controller.read_bytes()),
                    fetch_bytes=fetch_changed,
                )

            def fetch_unsigned(request_url: str, accept: str) -> bytes:
                if "/contents/" in request_url:
                    return expected
                return json.dumps(
                    {
                        "sha": commit,
                        "commit": {
                            "verification": {
                                "verified": False,
                                "reason": "unsigned",
                                "verified_at": None,
                            }
                        },
                    }
                ).encode()

            with self.assertRaisesRegex(ValueError, "not verified"):
                experiment_anchor.verify_external_anchor(
                    controller,
                    url,
                    expected_controller_sha256=sha(controller.read_bytes()),
                    fetch_bytes=fetch_unsigned,
                )

    def test_verify_rejects_controller_swap_and_self_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = write_controller(Path(directory))
            expected = experiment_anchor.canonical_json_bytes(
                experiment_anchor.build_anchor_request(controller)
            )
            commit = "e" * 40
            url = (
                "https://api.github.com/repos/coralehr/fhir-mcp-eval/contents/"
                f"anchors/a11b/anchor-request.json?ref={commit}"
            )
            fetch_calls = 0

            def should_not_fetch(_url: str, _accept: str) -> bytes:
                nonlocal fetch_calls
                fetch_calls += 1
                raise AssertionError("digest mismatch must fail before network access")

            with self.assertRaisesRegex(ValueError, "expected controller"):
                experiment_anchor.verify_external_anchor(
                    controller,
                    url,
                    expected_controller_sha256="0" * 64,
                    fetch_bytes=should_not_fetch,
                )
            self.assertEqual(fetch_calls, 0)

            with self.assertRaisesRegex(ValueError, "independent approval"):
                experiment_anchor.verify_external_anchor(
                    controller,
                    url,
                    expected_controller_sha256=sha(controller.read_bytes()),
                    fetch_bytes=verified_remote_fetch(
                        expected,
                        commit,
                        author="aanishs",
                        merger="aanishs",
                        reviewer="AANISHS",
                    ),
                )

            with self.assertRaisesRegex(ValueError, "independent approval"):
                experiment_anchor.verify_external_anchor(
                    controller,
                    url,
                    expected_controller_sha256=sha(controller.read_bytes()),
                    fetch_bytes=verified_remote_fetch(
                        expected,
                        commit,
                        reviewer="new-collaborator",
                        reviewer_id=999999,
                    ),
                )

            for identity_kwargs in (
                {
                    "author": "renamed-author",
                    "author_id": 143727238,
                },
                {
                    "merger": "renamed-merger",
                    "merger_id": 143727238,
                },
            ):
                with self.subTest(identity_kwargs=identity_kwargs):
                    with self.assertRaisesRegex(ValueError, "independent approval"):
                        experiment_anchor.verify_external_anchor(
                            controller,
                            url,
                            expected_controller_sha256=sha(
                                controller.read_bytes()
                            ),
                            fetch_bytes=verified_remote_fetch(
                                expected,
                                commit,
                                **identity_kwargs,
                            ),
                        )

            with self.assertRaisesRegex(ValueError, "independent approval"):
                experiment_anchor.verify_external_anchor(
                    controller,
                    url,
                    expected_controller_sha256=sha(controller.read_bytes()),
                    fetch_bytes=verified_remote_fetch(
                        expected,
                        commit,
                        reviewer="AJ112103",
                        reviewer_id=999999,
                    ),
                )

    def test_verify_ties_approval_to_exact_anchor_path_and_reviewed_head_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = write_controller(Path(directory))
            expected = experiment_anchor.canonical_json_bytes(
                experiment_anchor.build_anchor_request(controller)
            )
            commit = "f" * 40
            url = (
                "https://api.github.com/repos/coralehr/fhir-mcp-eval/contents/"
                f"anchors/a11b/anchor-request.json?ref={commit}"
            )

            with self.assertRaisesRegex(ValueError, "anchor path"):
                experiment_anchor.verify_external_anchor(
                    controller,
                    url,
                    expected_controller_sha256=sha(controller.read_bytes()),
                    fetch_bytes=verified_remote_fetch(
                        expected,
                        commit,
                        reviewed_path="anchors/unrelated/anchor-request.json",
                    ),
                )

            with self.assertRaisesRegex(ValueError, "reviewed PR head"):
                experiment_anchor.verify_external_anchor(
                    controller,
                    url,
                    expected_controller_sha256=sha(controller.read_bytes()),
                    fetch_bytes=verified_remote_fetch(
                        expected,
                        commit,
                        reviewed_head_bytes=expected + b" ",
                    ),
                )

    def test_verify_pagination_caps_fail_closed_and_page_two_can_succeed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = write_controller(Path(directory))
            expected = experiment_anchor.canonical_json_bytes(
                experiment_anchor.build_anchor_request(controller)
            )
            commit = "9" * 40
            url = (
                "https://api.github.com/repos/coralehr/fhir-mcp-eval/contents/"
                f"anchors/a11b/anchor-request.json?ref={commit}"
            )
            base_fetch = verified_remote_fetch(expected, commit)

            def capped(endpoint: str):
                def fetch(request_url: str, accept: str) -> bytes:
                    if endpoint in request_url:
                        return json.dumps([{}] * 100).encode()
                    return base_fetch(request_url, accept)

                return fetch

            for endpoint, label in (
                (f"/commits/{commit}/pulls?", "pull-request metadata"),
                ("/pulls/49/files?", "file metadata"),
                ("/pulls/49/reviews?", "review metadata"),
            ):
                with self.subTest(endpoint=endpoint):
                    with self.assertRaisesRegex(
                        ValueError,
                        rf"{label} exceeds page cap",
                    ):
                        experiment_anchor.verify_external_anchor(
                            controller,
                            url,
                            expected_controller_sha256=sha(
                                controller.read_bytes()
                            ),
                            fetch_bytes=capped(endpoint),
                        )

            def two_review_pages(request_url: str, accept: str) -> bytes:
                if "/pulls/49/reviews?" not in request_url:
                    return base_fetch(request_url, accept)
                if request_url.endswith("page=1"):
                    valid = json.loads(base_fetch(request_url, accept))
                    return json.dumps(valid + ([{}] * 99)).encode()
                if request_url.endswith("page=2"):
                    return b"[]"
                raise AssertionError(request_url)

            receipt = experiment_anchor.verify_external_anchor(
                controller,
                url,
                expected_controller_sha256=sha(controller.read_bytes()),
                fetch_bytes=two_review_pages,
            )
            self.assertEqual(receipt["independent_approver_ids"], [143727238])

    def test_verified_anchor_receipt_is_durable_and_resume_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = write_controller(root)
            expected = experiment_anchor.canonical_json_bytes(
                experiment_anchor.build_anchor_request(controller)
            )
            commit = "c" * 40
            url = (
                "https://api.github.com/repos/coralehr/fhir-mcp-eval/contents/"
                f"anchors/a11b/anchor-request.json?ref={commit}"
            )
            receipt_path = root / "external-anchor-verification.json"

            fetch_calls = 0
            remote_fetch = verified_remote_fetch(expected, commit)

            def fetch(request_url: str, accept: str) -> bytes:
                nonlocal fetch_calls
                fetch_calls += 1
                return remote_fetch(request_url, accept)

            first = experiment_anchor.verify_and_record_external_anchor(
                controller,
                url,
                receipt_path,
                expected_controller_sha256=sha(controller.read_bytes()),
                fetch_bytes=fetch,
            )
            first_fetch_calls = fetch_calls
            second = experiment_anchor.verify_and_record_external_anchor(
                controller,
                url,
                receipt_path,
                expected_controller_sha256=sha(controller.read_bytes()),
                fetch_bytes=fetch,
            )

            self.assertEqual(first, second)
            self.assertGreater(fetch_calls, first_fetch_calls)
            expected_receipt = experiment_anchor.canonical_json_bytes(first)
            self.assertEqual(receipt_path.read_bytes(), expected_receipt)
            self.assertEqual(
                receipt_path.with_suffix(".sha256").read_text(encoding="ascii"),
                sha(expected_receipt) + "\n",
            )
            receipt_path.with_suffix(".sha256").unlink()
            repaired = experiment_anchor.verify_and_record_external_anchor(
                controller,
                url,
                receipt_path,
                expected_controller_sha256=sha(controller.read_bytes()),
                fetch_bytes=fetch,
            )
            self.assertEqual(repaired, first)
            self.assertEqual(
                receipt_path.with_suffix(".sha256").read_text(encoding="ascii"),
                sha(expected_receipt) + "\n",
            )
            receipt_path.chmod(0o644)
            receipt_path.write_bytes(expected_receipt + b" ")
            with self.assertRaisesRegex(ValueError, "verification receipt changed"):
                experiment_anchor.verify_and_record_external_anchor(
                    controller,
                    url,
                    receipt_path,
                    expected_controller_sha256=sha(controller.read_bytes()),
                    fetch_bytes=fetch,
                )

    def test_receipt_contract_rejects_canonical_tampering_with_fresh_sidecar(
        self,
    ) -> None:
        cases = (
            ("unexpected", True, "schema"),
            ("anchor_path", "anchors/a11b/other.json", "reviewed file"),
            ("anchor_pr_head_file_sha256", "0" * 64, "reviewed file"),
            ("independent_approver_ids", [143709176], "approval"),
        )
        for field, value, error in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                controller = write_controller(root)
                expected = experiment_anchor.canonical_json_bytes(
                    experiment_anchor.build_anchor_request(controller)
                )
                commit = "8" * 40
                url = (
                    "https://api.github.com/repos/coralehr/fhir-mcp-eval/contents/"
                    f"anchors/a11b/anchor-request.json?ref={commit}"
                )
                receipt_path = root / "external-anchor-verification.json"
                remote_fetch = verified_remote_fetch(expected, commit)
                receipt = experiment_anchor.verify_and_record_external_anchor(
                    controller,
                    url,
                    receipt_path,
                    expected_controller_sha256=sha(controller.read_bytes()),
                    fetch_bytes=remote_fetch,
                )
                tampered = dict(receipt)
                tampered[field] = value
                payload = experiment_anchor.canonical_json_bytes(tampered)
                sidecar = receipt_path.with_suffix(".sha256")
                receipt_path.chmod(0o644)
                sidecar.chmod(0o644)
                receipt_path.write_bytes(payload)
                sidecar.write_text(sha(payload) + "\n", encoding="ascii")
                receipt_path.chmod(0o444)
                sidecar.chmod(0o444)

                with self.assertRaisesRegex(
                    ValueError,
                    rf"verification receipt changed: {error}",
                ):
                    experiment_anchor.verify_and_record_external_anchor(
                        controller,
                        url,
                        receipt_path,
                        expected_controller_sha256=sha(controller.read_bytes()),
                        fetch_bytes=remote_fetch,
                    )


if __name__ == "__main__":
    unittest.main()
