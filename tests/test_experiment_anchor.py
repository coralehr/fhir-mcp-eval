from __future__ import annotations

import hashlib
import json
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
