from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import codex_harness
import experiment_anchor
import experiment_executor as executor
import experiment_executor_service as service_module
import experiment_witness as witness
import trusted_codex_driver


RUN_ID = "1" * 64
HEAD = "2" * 64
NEXT_HEAD = "3" * 64


class FakeExecutor:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, str]] = []
        self.status_calls: list[str] = []
        self.fetch_calls = 0
        self.execute_error: BaseException | None = None

    def execute_next(self, *, run_id: str, expected_head: str) -> object:
        self.execute_calls.append((run_id, expected_head))
        if self.execute_error is not None:
            raise self.execute_error
        return SimpleNamespace(
            run_id=run_id,
            request_head=expected_head,
            witness_head=NEXT_HEAD,
            outcome="accepted",
            token_usage={
                "input": 10,
                "cached": 2,
                "output": 4,
                "reasoning": 1,
                "total": 14,
                "complete": True,
                "source": "turn.completed",
            },
            artifact_ref="must-not-cross-service-boundary",
            artifact_root_commitment="4" * 64,
            opened_receipt={"kind": "opened", "body": {"run_id": run_id}},
            closed_receipt={"kind": "closed", "body": {"outcome": "accepted"}},
            reason="accepted_complete_capture",
        )

    def status(self, *, run_id: str) -> dict[str, object]:
        self.status_calls.append(run_id)
        return {
            "run_id": run_id,
            "witness": {
                "run_id": run_id,
                "witness_key_id": "5" * 64,
                "head": NEXT_HEAD,
                "events": 2,
                "state": "active",
                "schedule_position": 1,
                "next_attempt_number": 1,
                "model_calls_reserved": 1,
                "model_calls_closed": 1,
            },
            "signed_receipts": [{"must": "not cross"}],
            "attempts": [{"must": "not cross"}],
        }

    def fetch_artifact(self, **_kwargs: object) -> bytes:
        self.fetch_calls += 1
        return b"must never be exposed"


class RestrictedExecutorServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = FakeExecutor()
        self.service = service_module.RestrictedExecutorService(self.executor)

    def _request(self, value: object) -> bytes:
        assert isinstance(value, dict)
        return service_module.canonical_json_line(
            {
                "kind": service_module.SERVICE_REQUEST_KIND,
                "schema_version": service_module.SERVICE_SCHEMA_VERSION,
                **value,
            }
        )

    def _response(self, payload: bytes) -> dict[str, object]:
        return json.loads(payload)

    def test_execute_exposes_receipts_and_economics_but_no_raw_artifact_handle(
        self,
    ) -> None:
        response = self._response(
            self.service.handle(
                self._request(
                    {
                        "operation": "execute_next",
                        "run_id": RUN_ID,
                        "expected_head": HEAD,
                    }
                )
            )
        )

        self.assertTrue(response["ok"])
        result = response["result"]
        self.assertEqual(result["witness_head"], NEXT_HEAD)
        self.assertEqual(result["token_usage"]["total"], 14)
        self.assertNotIn("artifact_ref", result)
        self.assertNotIn("must-not-cross-service-boundary", json.dumps(response))
        self.assertEqual(self.executor.execute_calls, [(RUN_ID, HEAD)])
        self.assertEqual(self.executor.fetch_calls, 0)

    def test_status_is_content_free_and_filters_executor_inventory(self) -> None:
        response = self._response(
            self.service.handle(
                self._request({"operation": "status", "run_id": RUN_ID})
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(
            set(response["result"]),
            {
                "kind",
                "run_id",
                "witness_head",
                "state",
                "schedule_position",
                "next_attempt_number",
                "model_calls_reserved",
                "model_calls_closed",
            },
        )
        self.assertNotIn("signed_receipts", json.dumps(response))
        self.assertNotIn("attempts", json.dumps(response))
        self.assertEqual(self.executor.status_calls, [RUN_ID])
        self.assertEqual(self.executor.fetch_calls, 0)

    def test_only_exact_canonical_execute_and_status_requests_are_accepted(self) -> None:
        invalid_requests = (
            self._request({"operation": "fetch_artifact", "run_id": RUN_ID}),
            (
                b'{"kind":"experiment_executor_service_request",'
                b'"operation":"status","operation":"status","run_id":"'
                + RUN_ID.encode()
                + b'","schema_version":"experiment-executor-service-v1"}\n'
            ),
            (
                b'{"kind":"experiment_executor_service_request", '
                b'"operation":"status","run_id":"'
                + RUN_ID.encode()
                + b'","schema_version":"experiment-executor-service-v1"}\n'
            ),
            self._request(
                {"operation": "status", "run_id": RUN_ID, "extra": "rejected"}
            ),
            self._request(
                {
                    "operation": "execute_next",
                    "run_id": RUN_ID,
                    "expected_head": HEAD,
                    "prompt": "caller-controlled",
                }
            ),
            self._request({"operation": "status", "run_id": "not-a-run-id"}),
            self._request({"operation": "status", "run_id": int("1" * 64)}),
            self._request(
                {
                    "operation": "execute_next",
                    "run_id": RUN_ID,
                    "expected_head": int("2" * 64),
                }
            ),
            b"{}\n{}\n",
            b"\xff\n",
            b"x" * (service_module.MAX_REQUEST_BYTES + 1),
        )
        for payload in invalid_requests:
            with self.subTest(payload=payload[:80]):
                response = self._response(self.service.handle(payload))
                self.assertEqual(
                    response,
                    {
                        "error": {"code": "invalid_request"},
                        "ok": False,
                        "schema_version": service_module.SERVICE_SCHEMA_VERSION,
                    },
                )
        self.assertEqual(self.executor.execute_calls, [])
        self.assertEqual(self.executor.status_calls, [])
        self.assertEqual(self.executor.fetch_calls, 0)

    def test_executor_failures_are_canonical_and_redacted(self) -> None:
        cases = (
            (
                executor.ExecutorProtocolError("secret prompt /private/path"),
                "protocol_error",
            ),
            (
                executor.ExecutorIntegrityError("secret prompt /private/path"),
                "integrity_error",
            ),
            (
                executor.ExecutorIndeterminateError("secret prompt /private/path"),
                "indeterminate",
            ),
            (RuntimeError("secret prompt /private/path"), "internal_error"),
        )
        request = self._request(
            {
                "operation": "execute_next",
                "run_id": RUN_ID,
                "expected_head": HEAD,
            }
        )
        for failure, code in cases:
            with self.subTest(code=code):
                self.executor.execute_error = failure
                raw = self.service.handle(request)
                response = self._response(raw)
                self.assertEqual(response["error"], {"code": code})
                self.assertNotIn("secret", raw.decode())
                self.assertNotIn("private", raw.decode())
                self.assertEqual(raw, service_module.canonical_json_line(response))

    def test_terminal_indeterminate_result_returns_signed_new_head(self) -> None:
        terminal = self.executor.execute_next(run_id=RUN_ID, expected_head=HEAD)
        terminal.outcome = "indeterminate"
        terminal.reason = "spawn_intent_without_durable_capture"
        terminal.token_usage = {
            "input": None,
            "cached": None,
            "output": None,
            "reasoning": None,
            "total": None,
            "complete": False,
            "source": "unavailable",
        }
        self.executor.execute_calls.clear()
        self.executor.execute_error = executor.ExecutorIndeterminateError(
            "private path must not cross",
            result=terminal,
        )

        response = self._response(
            self.service.handle(
                self._request(
                    {
                        "operation": "execute_next",
                        "run_id": RUN_ID,
                        "expected_head": HEAD,
                    }
                )
            )
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["outcome"], "indeterminate")
        self.assertEqual(response["result"]["witness_head"], NEXT_HEAD)
        self.assertNotIn("artifact_ref", response["result"])
        self.assertNotIn("private", json.dumps(response))

    def test_serve_once_reads_one_bounded_request_and_emits_one_response(self) -> None:
        request = self._request({"operation": "status", "run_id": RUN_ID})
        stdin = io.BytesIO(request)
        stdout = io.BytesIO()

        exit_code = service_module.serve_once(self.service, stdin, stdout)

        self.assertEqual(exit_code, 0)
        response = self._response(stdout.getvalue())
        self.assertTrue(response["ok"])
        self.assertEqual(stdout.getvalue(), service_module.canonical_json_line(response))


class FakeTrustedDriver:
    constructions: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.constructions.append(kwargs)

    def invoke(
        self, invocation: executor.SealedInvocation, capture_dir: Path
    ) -> executor.DriverTermination:
        raise AssertionError("loader tests must not execute a model driver")


class SealedBundleLoaderTests(unittest.TestCase):
    def _private_dir(self, parent: Path, name: str) -> Path:
        path = parent / name
        path.mkdir(mode=0o700)
        path.chmod(0o700)
        return path

    def _sealed_file(self, path: Path, payload: bytes, mode: int) -> None:
        if path.exists():
            path.chmod(0o600)
        path.write_bytes(payload)
        path.chmod(mode)

    def _receipt(self, path: Path) -> dict[str, object]:
        payload = path.read_bytes()
        return {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }

    def _code_subjects(self) -> list[dict[str, object]]:
        paths = {
            "anchor": Path(experiment_anchor.__file__).resolve(),
            "bootstrap": Path(service_module.__file__).with_name(
                "experiment_executor_bootstrap.py"
            ),
            "codex_harness": Path(codex_harness.__file__).resolve(),
            "driver": Path(trusted_codex_driver.__file__).resolve(),
            "executor": Path(executor.__file__).resolve(),
            "service": Path(service_module.__file__).resolve(),
            "witness": Path(witness.__file__).resolve(),
        }
        result = []
        for name, path in sorted(paths.items()):
            payload = path.read_bytes()
            result.append(
                {
                    "name": name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                }
            )
        return result

    def _reseal_bundle(self, bundle_dir: Path, bundle: dict[str, object]) -> None:
        bundle_bytes = service_module.canonical_json_line(bundle)
        self._sealed_file(bundle_dir / "bundle.json", bundle_bytes, 0o400)
        commitment_key = (bundle_dir / "commitment.key").read_bytes()
        locator_path = bundle_dir / "anchor-locator.json"
        locator = json.loads(locator_path.read_bytes())
        locator["bundle_commitment"] = witness.keyed_commitment(
            commitment_key,
            domain="executor-bundle",
            payload=bundle_bytes,
        )
        self._sealed_file(
            locator_path, service_module.canonical_json_line(locator), 0o400
        )

    def _controller_bytes(self, binding: dict[str, object]) -> bytes:
        model_configuration = binding["model_configuration"]
        assert isinstance(model_configuration, dict)
        answer_configuration = model_configuration["answer"]
        panel_configuration = model_configuration["panel"]
        snapshots = {
            name: {
                "sha256": hashlib.sha256(name.encode()).hexdigest(),
                "bytes": len(name),
            }
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
            "schema_version": "a11-controller-v4",
            "experiment_profile": "a11b-causal-isolation-v2",
            "execution": {
                **answer_configuration,
                "codex": {
                    "native": {
                        "path": binding["runtime"]["path"],
                        "sha256": binding["runtime"]["sha256"],
                        "bytes": binding["runtime"]["bytes"],
                    }
                },
                "trusted_executor": binding,
            },
            "grading": {
                "panel": panel_configuration
            },
            "snapshots": snapshots,
        }
        return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()

    def _fixture(
        self, root: Path
    ) -> tuple[Path, dict[str, object], dict[str, object]]:
        bundle_dir = self._private_dir(root, "bundle")
        state = self._private_dir(bundle_dir, "state")
        self._private_dir(state, "witness")
        self._private_dir(state, "executor")
        codex_home = self._private_dir(bundle_dir, "codex-home")
        self._sealed_file(codex_home / "auth.json", b"credential-sentinel", 0o600)
        self._private_dir(bundle_dir, "scratch")
        self._private_dir(bundle_dir / "scratch", "service-tmp")

        commitment_key = bytes(range(32))
        self._sealed_file(bundle_dir / "commitment.key", commitment_key, 0o600)
        private_key = bundle_dir / "witness_ed25519"
        witness.subprocess.run(
            [
                str(witness.SSH_KEYGEN_PATH),
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "sealed-service-test",
                "-f",
                str(private_key),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        private_key.chmod(0o600)
        authenticator = witness.SshEd25519Authenticator(
            private_key_path=private_key,
            identity="sealed-service-test",
        )

        runtime = bundle_dir / "codex-native"
        shutil.copyfile("/usr/bin/true", runtime)
        runtime.chmod(0o500)
        sandbox_exec = bundle_dir / "sandbox-exec"
        shutil.copyfile("/usr/bin/true", sandbox_exec)
        sandbox_exec.chmod(0o500)

        output_schema = json.dumps(
            {
                "type": "object",
                "required": ["answer"],
                "additionalProperties": False,
                "properties": {"answer": {"type": "string"}},
            },
            sort_keys=True,
        ).encode()
        invocation = executor.SealedInvocation(
            phase="answer",
            schedule_index=0,
            prompt=b"sealed synthetic prompt",
            output_schema=output_schema,
            model="gpt-test-model",
            reasoning_effort="high",
            runtime_path=str(runtime),
            runtime_sha256=hashlib.sha256(runtime.read_bytes()).hexdigest(),
            timeout_seconds=17,
        )
        schedule = [
            {
                "phase": "answer",
                "schedule_index": 0,
                "call_commitment": invocation.call_commitment(commitment_key),
                "max_attempts": 1,
            }
        ]
        python_path = Path(sys.executable).resolve()
        bundle = {
            "kind": "experiment_executor_service_bundle",
            "schema_version": service_module.BUNDLE_SCHEMA_VERSION,
            "service_protocol_version": service_module.SERVICE_SCHEMA_VERSION,
            "run_id": RUN_ID,
            "witness": {
                "identity": authenticator.identity,
                "public_key": authenticator.public_key,
                "key_id": authenticator.key_id,
                "schedule": schedule,
            },
            "runtime": {
                **self._receipt(runtime),
                "version": "codex-cli 0.test",
            },
            "sandbox": {
                **self._receipt(sandbox_exec),
                "profile": trusted_codex_driver.TrustedCodexDriver._SANDBOX_PROFILE,
            },
            "executables": {
                "python": self._receipt(python_path),
                "ssh_keygen": self._receipt(witness.SSH_KEYGEN_PATH),
            },
            "code_subjects": self._code_subjects(),
            "model_configuration": {
                "answer": {
                    "model": invocation.model,
                    "reasoning_effort": invocation.reasoning_effort,
                    "timeout_seconds": invocation.timeout_seconds,
                },
                "panel": {
                    "model": "judge-test",
                    "reasoning_effort": "high",
                    "votes": 3,
                    "batch_size": 20,
                    "timeout_seconds": 600,
                },
            },
            "anchor_verifier": service_module.ANCHOR_CHECKER_VERIFIER,
            "invocations": [
                {
                    "phase": "answer",
                    "schedule_index": 0,
                    "prompt_base64": base64.b64encode(invocation.prompt).decode("ascii"),
                    "output_schema_base64": base64.b64encode(output_schema).decode(
                        "ascii"
                    ),
                    "model": invocation.model,
                    "reasoning_effort": invocation.reasoning_effort,
                    "timeout_seconds": invocation.timeout_seconds,
                }
            ],
        }
        bundle_bytes = service_module.canonical_json_line(bundle)
        self._sealed_file(bundle_dir / "bundle.json", bundle_bytes, 0o400)
        bundle_commitment = witness.keyed_commitment(
            commitment_key,
            domain="executor-bundle",
            payload=bundle_bytes,
        )
        public_binding = {
            "bundle_commitment": bundle_commitment,
            "bundle_schema_version": service_module.BUNDLE_SCHEMA_VERSION,
            "service_protocol_version": service_module.SERVICE_SCHEMA_VERSION,
            "run_id": RUN_ID,
            "witness": bundle["witness"],
            "runtime": bundle["runtime"],
            "sandbox": bundle["sandbox"],
            "executables": bundle["executables"],
            "code_subjects": bundle["code_subjects"],
            "model_configuration": bundle["model_configuration"],
            "anchor_verifier": bundle["anchor_verifier"],
        }
        controller_bytes = self._controller_bytes(public_binding)
        self._sealed_file(bundle_dir / "controller.json", controller_bytes, 0o400)
        locator = {
            "kind": "experiment_executor_anchor_locator",
            "schema_version": service_module.ANCHOR_LOCATOR_SCHEMA_VERSION,
            "anchor_url": (
                "https://api.github.com/repos/coralehr/fhir-mcp-eval/contents/"
                "anchors/synthetic-a11b.json?ref=" + "a" * 40
            ),
            "controller_sha256": hashlib.sha256(controller_bytes).hexdigest(),
            "bundle_commitment": bundle_commitment,
        }
        self._sealed_file(
            bundle_dir / "anchor-locator.json",
            service_module.canonical_json_line(locator),
            0o400,
        )
        self._sealed_file(
            bundle_dir / "external-anchor-verification.json",
            service_module.canonical_json_line({"synthetic": True}),
            0o400,
        )
        return bundle_dir, bundle, public_binding

    def _anchor_result(self, public_binding: dict[str, object]) -> dict[str, object]:
        runtime = public_binding["runtime"]
        assert isinstance(runtime, dict)
        return {
            "trusted_executor": public_binding,
            "model_configuration": public_binding["model_configuration"],
            "native_codex": {
                "sha256": runtime["sha256"],
                "bytes": runtime["bytes"],
            },
        }

    def test_default_anchor_validator_requires_v4_exact_service_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle_dir, _bundle, public_binding = self._fixture(root)
            controller_bytes = (bundle_dir / "controller.json").read_bytes()
            locator = json.loads((bundle_dir / "anchor-locator.json").read_bytes())

            with mock.patch.object(
                experiment_anchor,
                "verify_signed_external_anchor_receipt",
                return_value={"verified": True},
            ) as verify:
                actual = service_module._validate_recorded_service_anchor(
                    controller_bytes=controller_bytes,
                    anchor_url=locator["anchor_url"],
                    receipt_bytes=(
                        bundle_dir / "external-anchor-verification.json"
                    ).read_bytes(),
                    expected_controller_sha256=locator["controller_sha256"],
                )

            self.assertEqual(actual, self._anchor_result(public_binding))
            verify.assert_called_once()
            self.assertEqual(
                verify.call_args.kwargs["expected_verifier"],
                service_module.ANCHOR_CHECKER_VERIFIER,
            )

    def test_loader_constructs_every_secret_and_model_input_from_fixed_bundle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle_dir, _bundle, public_binding = self._fixture(root)
            FakeTrustedDriver.constructions.clear()

            restricted = service_module.load_sealed_service(
                bundle_dir,
                anchor_validator=lambda **_kwargs: self._anchor_result(public_binding),
                driver_factory=FakeTrustedDriver,
                code_subject_provider=self._code_subjects,
                clock=lambda: "2026-07-15T23:00:00Z",
            )
            response = json.loads(
                restricted.handle(
                    service_module.canonical_json_line(
                        {
                            "kind": service_module.SERVICE_REQUEST_KIND,
                            "schema_version": service_module.SERVICE_SCHEMA_VERSION,
                            "operation": "status",
                            "run_id": RUN_ID,
                        }
                    )
                )
            )

            self.assertTrue(response["ok"])
            self.assertEqual(response["result"]["model_calls_reserved"], 0)
            self.assertEqual(len(FakeTrustedDriver.constructions), 1)
            construction = FakeTrustedDriver.constructions[0]
            self.assertEqual(construction["account_home"], bundle_dir)
            self.assertEqual(construction["codex_home"], bundle_dir / "codex-home")
            self.assertEqual(construction["scratch_root"], bundle_dir / "scratch")

    def test_loader_rejects_anchor_or_schedule_mismatch_before_driver_construction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle_dir, bundle, public_binding = self._fixture(root)
            cases = ("anchor", "schedule")
            for case in cases:
                with self.subTest(case=case):
                    FakeTrustedDriver.constructions.clear()
                    if case == "anchor":
                        binding = self._anchor_result(
                            {**public_binding, "run_id": "f" * 64}
                        )
                    else:
                        binding = self._anchor_result(public_binding)
                        changed = json.loads(json.dumps(bundle))
                        changed["invocations"][0]["model"] = "changed-model"
                        self._reseal_bundle(bundle_dir, changed)
                    with self.assertRaises(service_module.ServiceBootstrapError):
                        service_module.load_sealed_service(
                            bundle_dir,
                            anchor_validator=lambda **_kwargs: binding,
                            driver_factory=FakeTrustedDriver,
                            code_subject_provider=self._code_subjects,
                            clock=lambda: "2026-07-15T23:00:00Z",
                        )
                    self.assertEqual(FakeTrustedDriver.constructions, [])
                    if case == "schedule":
                        self._reseal_bundle(bundle_dir, bundle)

    def test_loader_rejects_invocation_configuration_outside_bound_phase(self) -> None:
        mutations = {
            "model": "changed-model",
            "reasoning_effort": "medium",
            "timeout_seconds": 18,
        }
        for field, value in mutations.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                bundle_dir, bundle, _public_binding = self._fixture(root)
                changed = json.loads(json.dumps(bundle))
                changed["invocations"][0][field] = value
                runtime = changed["runtime"]
                invocation_value = changed["invocations"][0]
                invocation = executor.SealedInvocation(
                    phase=invocation_value["phase"],
                    schedule_index=invocation_value["schedule_index"],
                    prompt=base64.b64decode(invocation_value["prompt_base64"]),
                    output_schema=base64.b64decode(
                        invocation_value["output_schema_base64"]
                    ),
                    model=invocation_value["model"],
                    reasoning_effort=invocation_value["reasoning_effort"],
                    runtime_path=runtime["path"],
                    runtime_sha256=runtime["sha256"],
                    timeout_seconds=invocation_value["timeout_seconds"],
                )
                commitment_key = (bundle_dir / "commitment.key").read_bytes()
                changed["witness"]["schedule"][0][
                    "call_commitment"
                ] = invocation.call_commitment(commitment_key)
                self._reseal_bundle(bundle_dir, changed)
                bundle_commitment = json.loads(
                    (bundle_dir / "anchor-locator.json").read_bytes()
                )["bundle_commitment"]
                changed_binding = service_module._public_binding(
                    changed,
                    bundle_commitment=bundle_commitment,
                )
                FakeTrustedDriver.constructions.clear()

                with self.assertRaisesRegex(
                    service_module.ServiceBootstrapError,
                    "differs from model configuration",
                ):
                    service_module.load_sealed_service(
                        bundle_dir,
                        anchor_validator=lambda **_kwargs: self._anchor_result(
                            changed_binding
                        ),
                        driver_factory=FakeTrustedDriver,
                        code_subject_provider=self._code_subjects,
                        clock=lambda: "2026-07-15T23:00:00Z",
                    )
                self.assertEqual(FakeTrustedDriver.constructions, [])

    def test_loader_rejects_substituted_anchor_verifier_before_driver(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle_dir, bundle, _public_binding = self._fixture(root)
            changed = json.loads(json.dumps(bundle))
            changed["anchor_verifier"]["identity"] = "attacker-checker"
            self._reseal_bundle(bundle_dir, changed)
            FakeTrustedDriver.constructions.clear()
            with self.assertRaisesRegex(
                service_module.ServiceBootstrapError,
                "anchor verifier changed",
            ):
                service_module.load_sealed_service(
                    bundle_dir,
                    anchor_validator=lambda **_kwargs: {},
                    driver_factory=FakeTrustedDriver,
                    code_subject_provider=self._code_subjects,
                    clock=lambda: "2026-07-15T23:00:00Z",
                )
            self.assertEqual(FakeTrustedDriver.constructions, [])

    def test_loader_rejects_unsafe_private_file_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bundle_dir, _bundle, public_binding = self._fixture(root)
            commitment_key = bundle_dir / "commitment.key"
            commitment_key.chmod(0o644)
            FakeTrustedDriver.constructions.clear()

            with self.assertRaises(service_module.ServiceBootstrapError):
                service_module.load_sealed_service(
                    bundle_dir,
                    anchor_validator=lambda **_kwargs: self._anchor_result(public_binding),
                    driver_factory=FakeTrustedDriver,
                    code_subject_provider=self._code_subjects,
                    clock=lambda: "2026-07-15T23:00:00Z",
                )
            self.assertEqual(FakeTrustedDriver.constructions, [])

    def test_loader_rejects_symlinks_hardlinks_writable_ancestors_and_threads(
        self,
    ) -> None:
        mutations = ("bundle_symlink", "key_hardlink", "writable_ancestor", "threads")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                bundle_dir, _bundle, public_binding = self._fixture(root)
                load_path = bundle_dir
                thread_patch = mock.patch.object(
                    service_module.threading, "active_count", return_value=1
                )
                if mutation == "bundle_symlink":
                    load_path = root / "bundle-link"
                    load_path.symlink_to(bundle_dir)
                elif mutation == "key_hardlink":
                    os.link(bundle_dir / "commitment.key", bundle_dir / "key-link")
                elif mutation == "writable_ancestor":
                    root.chmod(0o777)
                else:
                    thread_patch = mock.patch.object(
                        service_module.threading, "active_count", return_value=2
                    )
                FakeTrustedDriver.constructions.clear()
                try:
                    with thread_patch, self.assertRaises(
                        service_module.ServiceBootstrapError
                    ):
                        service_module.load_sealed_service(
                            load_path,
                            anchor_validator=lambda **_kwargs: self._anchor_result(public_binding),
                            driver_factory=FakeTrustedDriver,
                            code_subject_provider=self._code_subjects,
                            clock=lambda: "2026-07-15T23:00:00Z",
                        )
                finally:
                    root.chmod(0o700)
                self.assertEqual(FakeTrustedDriver.constructions, [])

    def test_loader_rejects_derived_witness_or_code_identity_mismatch(self) -> None:
        for mutation in ("witness", "code"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                bundle_dir, bundle, public_binding = self._fixture(root)
                changed = json.loads(json.dumps(bundle))
                if mutation == "witness":
                    changed["witness"]["public_key"] = "ssh-ed25519 invalid"
                else:
                    changed["code_subjects"][0]["sha256"] = "f" * 64
                self._reseal_bundle(bundle_dir, changed)
                FakeTrustedDriver.constructions.clear()

                with self.assertRaises(service_module.ServiceBootstrapError):
                    service_module.load_sealed_service(
                        bundle_dir,
                        anchor_validator=lambda **_kwargs: self._anchor_result(public_binding),
                        driver_factory=FakeTrustedDriver,
                        code_subject_provider=self._code_subjects,
                        clock=lambda: "2026-07-15T23:00:00Z",
                    )
                self.assertEqual(FakeTrustedDriver.constructions, [])

    def test_main_uses_only_fixed_bundle_root_and_redacts_bootstrap_failure(self) -> None:
        fake_service = object()
        stdout = io.BytesIO()
        with (
            mock.patch.object(service_module, "PRODUCTION_BUNDLE_DIR", Path("/fixed")),
            mock.patch.object(
                service_module,
                "load_sealed_service",
                return_value=fake_service,
            ) as load,
            mock.patch.object(service_module, "serve_once", return_value=0) as serve,
            mock.patch.object(service_module.sys, "stdin", SimpleNamespace(buffer=io.BytesIO())),
            mock.patch.object(service_module.sys, "stdout", SimpleNamespace(buffer=stdout)),
            mock.patch.object(service_module.sys, "argv", ["service", "/caller/path"]),
            mock.patch.object(service_module, "_install_shutdown_handlers"),
            mock.patch.object(service_module, "_require_production_process"),
            mock.patch.dict(
                os.environ,
                {"SSH_ORIGINAL_COMMAND": "fetch_artifact", "BUNDLE": "/caller/path"},
            ),
        ):
            self.assertEqual(service_module.main(), 0)
        load.assert_called_once()
        self.assertEqual(load.call_args.args, (Path("/fixed"),))
        self.assertEqual(set(load.call_args.kwargs), {"clock"})
        serve.assert_called_once()

        secret = "secret prompt /private/path"
        with (
            mock.patch.object(
                service_module,
                "load_sealed_service",
                side_effect=RuntimeError(secret),
            ),
            mock.patch.object(service_module.sys, "stdout", SimpleNamespace(buffer=stdout)),
            mock.patch.object(service_module, "_install_shutdown_handlers"),
            mock.patch.object(service_module, "_require_production_process"),
        ):
            stdout.seek(0)
            stdout.truncate(0)
            self.assertEqual(service_module.main(), 1)
        self.assertNotIn("secret", stdout.getvalue().decode())
        self.assertNotIn("private", stdout.getvalue().decode())
        self.assertEqual(
            json.loads(stdout.getvalue())["error"], {"code": "bootstrap_failure"}
        )

    def test_main_turns_shutdown_signal_into_process_exit_without_error_egress(
        self,
    ) -> None:
        with (
            mock.patch.object(service_module, "_install_shutdown_handlers"),
            mock.patch.object(service_module, "_require_production_process"),
            mock.patch.object(
                service_module, "load_sealed_service", return_value=object()
            ),
            mock.patch.object(
                service_module,
                "serve_once",
                side_effect=service_module._ServiceShutdown(signal.SIGTERM),
            ),
        ):
            self.assertEqual(service_module.main(), 128 + signal.SIGTERM)

    def test_main_contains_bootstrap_signals_and_client_disconnects(self) -> None:
        for phase in ("handlers", "bootstrap"):
            with self.subTest(phase=phase):
                install_effect = (
                    service_module._ServiceShutdown(signal.SIGHUP)
                    if phase == "handlers"
                    else None
                )
                load_effect = (
                    service_module._ServiceShutdown(signal.SIGTERM)
                    if phase == "bootstrap"
                    else None
                )
                with (
                    mock.patch.object(
                        service_module,
                        "_install_shutdown_handlers",
                        side_effect=install_effect,
                    ),
                    mock.patch.object(service_module, "_require_production_process"),
                    mock.patch.object(
                        service_module,
                        "load_sealed_service",
                        side_effect=load_effect,
                        return_value=object(),
                    ),
                ):
                    expected = (
                        128 + signal.SIGHUP
                        if phase == "handlers"
                        else 128 + signal.SIGTERM
                    )
                    self.assertEqual(service_module.main(), expected)

        broken = mock.Mock()
        broken.write.side_effect = BrokenPipeError("private path")
        with (
            mock.patch.object(service_module, "_install_shutdown_handlers"),
            mock.patch.object(service_module, "_require_production_process"),
            mock.patch.object(
                service_module,
                "load_sealed_service",
                side_effect=RuntimeError("private path"),
            ),
            mock.patch.object(
                service_module.sys,
                "stdout",
                SimpleNamespace(buffer=broken),
            ),
        ):
            self.assertEqual(service_module.main(), 1)
        broken.write.assert_called_once()

        with (
            mock.patch.object(service_module, "_install_shutdown_handlers"),
            mock.patch.object(service_module, "_require_production_process"),
            mock.patch.object(
                service_module, "load_sealed_service", return_value=object()
            ),
            mock.patch.object(
                service_module,
                "serve_once",
                side_effect=BrokenPipeError("private path"),
            ),
        ):
            self.assertEqual(service_module.main(), 1)

    def test_production_process_requires_fixed_isolated_environment(self) -> None:
        with self.assertRaises(service_module.ServiceBootstrapError):
            service_module._require_production_process()

        flags = SimpleNamespace(isolated=1, dont_write_bytecode=1, no_site=1)
        with (
            mock.patch.object(service_module.sys, "flags", flags),
            mock.patch.object(
                service_module.sys,
                "argv",
                [str(service_module.PRODUCTION_SERVICE_PATH)],
            ),
            mock.patch.object(
                service_module.sys,
                "executable",
                str(service_module.PRODUCTION_PYTHON_PATH),
            ),
            mock.patch.object(
                service_module.sys,
                "path",
                ["/stdlib", str(service_module.PRODUCTION_CODE_DIR)],
            ),
            mock.patch.object(
                service_module.Path,
                "cwd",
                return_value=service_module.PRODUCTION_BUNDLE_DIR,
            ),
            mock.patch.object(
                service_module.tempfile,
                "gettempdir",
                return_value=str(service_module.PRODUCTION_TMPDIR),
            ),
            mock.patch.dict(
                service_module.os.environ,
                service_module.PRODUCTION_ENVIRONMENT,
                clear=True,
            ),
            mock.patch.object(
                service_module.resource,
                "getrlimit",
                return_value=(0, 0),
            ),
            mock.patch.object(
                service_module.os,
                "umask",
                side_effect=(0o077, None),
            ),
        ):
            service_module._require_production_process()


if __name__ == "__main__":
    unittest.main()
