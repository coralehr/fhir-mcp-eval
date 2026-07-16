from __future__ import annotations

import json
import unittest
from dataclasses import replace
from typing import Callable

from a11_evidence_core import canonical_bytes, sha256
from provider_api_contract import (
    AdapterFailureExchange,
    AdapterExchange,
    CanonicalProviderResult,
    ProviderIndeterminateError,
    ProviderIntegrityError,
    ProviderProtocolError,
    SealedProviderRequest,
    execute_sealed_request,
    verify_provider_result,
)


SCHEMA = canonical_bytes(
    {
        "type": "object",
        "required": ["answer"],
        "additionalProperties": False,
        "properties": {"answer": {"type": "string"}},
    }
)


def _profile(provider: str = "openai") -> bytes:
    finish_reason = "stop" if provider == "openai" else "end_turn"
    return canonical_bytes(
        {
            "schema_version": "provider-api-profile-v1",
            "provider": provider,
            "endpoint": f"https://api.{provider}.example/v1/messages",
            "api_surface": f"{provider}-messages-v1",
            "model": f"{provider}-test-snapshot",
            "adapter": {
                "name": f"{provider}-direct-adapter",
                "version": "1.0.0",
                "source_sha256": "1" * 64,
                "dependencies": [
                    {"name": f"{provider}-sdk", "version": "1.2.3", "sha256": "2" * 64}
                ],
            },
            "request_parameters": {
                "max_output_tokens": 512,
                "temperature": 0,
                "tool_policy": "forbidden",
            },
            "usage_mapping": f"{provider}-usage-v1",
            "accepted_finish_reasons": [finish_reason],
            "retryable_http_statuses": [408, 409, 429, 500, 502, 503, 504],
            "pricing_snapshot_sha256": "3" * 64,
            "idempotency": {
                "supported": True,
                "header": "idempotency-key",
                "retry_policy": "same-key-only",
            },
        }
    )


def _request(profile: bytes | None = None) -> SealedProviderRequest:
    return SealedProviderRequest(
        phase="answer",
        schedule_index=0,
        attempt_number=1,
        profile=profile or _profile(),
        prompt=b"sealed prompt and packet bytes",
        output_schema=SCHEMA,
        idempotency_key="a11b-answer-0000-attempt-1",
        timeout_seconds=600,
    )


class OpenAIShapedFakeAdapter:
    def __init__(self, profile: bytes) -> None:
        self.profile_sha256 = sha256(profile)
        self.source_sha256 = "1" * 64
        self.calls = 0

    def invoke(self, request: SealedProviderRequest) -> AdapterExchange:
        self.calls += 1
        answer = canonical_bytes({"answer": "synthetic"})
        return AdapterExchange(
            profile_sha256=self.profile_sha256,
            idempotency_key_sha256=sha256(request.idempotency_key.encode()),
            request_body=canonical_bytes(
                {"model": "openai-test-snapshot", "input": "sealed"}
            ),
            response_body=canonical_bytes(
                {"id": "resp_test_001", "output_text": answer.decode()}
            ),
            normalized_answer=answer,
            http_status=200,
            response_id="resp_test_001",
            provider_request_id="req_test_001",
            returned_model="openai-test-snapshot",
            finish_reason="stop",
            tool_calls=0,
            latency_ms=12,
            usage={
                "input": 20,
                "cached": 4,
                "output": 5,
                "reasoning": 2,
                "total": 25,
                "complete": True,
                "source": "provider.api",
            },
        )


class AnthropicShapedFakeAdapter:
    def __init__(self, profile: bytes) -> None:
        self.profile_sha256 = sha256(profile)
        self.source_sha256 = "1" * 64
        self.calls = 0

    def invoke(self, request: SealedProviderRequest) -> AdapterExchange:
        self.calls += 1
        answer = canonical_bytes({"answer": "synthetic"})
        return AdapterExchange(
            profile_sha256=self.profile_sha256,
            idempotency_key_sha256=sha256(request.idempotency_key.encode()),
            request_body=canonical_bytes(
                {"model": "anthropic-test-snapshot", "messages": ["sealed"]}
            ),
            response_body=canonical_bytes(
                {
                    "id": "msg_test_001",
                    "content": [{"type": "text", "text": answer.decode()}],
                }
            ),
            normalized_answer=answer,
            http_status=200,
            response_id="msg_test_001",
            provider_request_id="req_test_002",
            returned_model="anthropic-test-snapshot",
            finish_reason="end_turn",
            tool_calls=0,
            latency_ms=15,
            usage={
                "input": 30,
                "cached": 0,
                "output": 4,
                "reasoning": 0,
                "total": 34,
                "complete": True,
                "source": "provider.api",
            },
        )


class MutatingFakeAdapter(OpenAIShapedFakeAdapter):
    def __init__(
        self,
        profile: bytes,
        mutate: Callable[[AdapterExchange], AdapterExchange],
    ) -> None:
        super().__init__(profile)
        self.mutate = mutate

    def invoke(self, request: SealedProviderRequest) -> AdapterExchange:
        return self.mutate(super().invoke(request))


class RaisingFakeAdapter(OpenAIShapedFakeAdapter):
    def invoke(self, request: SealedProviderRequest) -> AdapterExchange:
        self.calls += 1
        raise TimeoutError("synthetic timeout after send")


class InterruptingFakeAdapter(OpenAIShapedFakeAdapter):
    def invoke(self, request: SealedProviderRequest) -> AdapterExchange:
        self.calls += 1
        raise KeyboardInterrupt


class RateLimitedFakeAdapter(OpenAIShapedFakeAdapter):
    def invoke(self, request: SealedProviderRequest) -> AdapterFailureExchange:
        self.calls += 1
        return AdapterFailureExchange(
            profile_sha256=self.profile_sha256,
            idempotency_key_sha256=sha256(request.idempotency_key.encode()),
            request_body=canonical_bytes(
                {"model": "openai-test-snapshot", "input": "sealed"}
            ),
            response_body=canonical_bytes(
                {"error": {"code": "rate_limit", "message": "synthetic"}}
            ),
            http_status=429,
            response_id=None,
            provider_request_id="req_rate_limit_001",
            error_code="rate_limit",
            retryable=True,
            latency_ms=9,
            usage={
                "input": None,
                "cached": None,
                "output": None,
                "reasoning": None,
                "total": None,
                "complete": False,
                "source": "provider.error",
            },
        )


class MutatingFailureAdapter(RateLimitedFakeAdapter):
    def __init__(
        self,
        profile: bytes,
        mutate: Callable[[AdapterFailureExchange], AdapterFailureExchange],
    ) -> None:
        super().__init__(profile)
        self.mutate = mutate

    def invoke(self, request: SealedProviderRequest) -> AdapterFailureExchange:
        return self.mutate(super().invoke(request))


class ProviderApiContractTests(unittest.TestCase):
    def test_executes_one_sealed_request_and_returns_canonical_receipt(self) -> None:
        profile = _profile()
        request = _request(profile)
        adapter = OpenAIShapedFakeAdapter(profile)

        result = execute_sealed_request(request, adapter)

        self.assertIsInstance(result, CanonicalProviderResult)
        self.assertEqual(json.loads(result.answer), {"answer": "synthetic"})
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(result.receipt["provider"], "openai")
        self.assertEqual(result.receipt["usage"]["total"], 25)
        self.assertEqual(result.receipt["transport"]["tool_calls"], 0)
        receipt_text = json.dumps(result.receipt, sort_keys=True)
        self.assertNotIn("sealed prompt", receipt_text)
        self.assertNotIn("synthetic", receipt_text)
        self.assertNotIn(request.idempotency_key, receipt_text)

    def test_two_provider_shaped_adapters_conform_to_one_interface(self) -> None:
        cases = (
            ("openai", OpenAIShapedFakeAdapter),
            ("anthropic", AnthropicShapedFakeAdapter),
        )
        for provider, adapter_class in cases:
            with self.subTest(provider=provider):
                profile = _profile(provider)
                request = _request(profile)
                adapter = adapter_class(profile)

                result = execute_sealed_request(request, adapter)

                self.assertEqual(result.receipt["provider"], provider)
                self.assertEqual(result.receipt["adapter_invocations"], 1)
                self.assertEqual(adapter.calls, 1)

    def test_profile_or_source_mismatch_fails_before_adapter_call(self) -> None:
        profile = _profile()
        request = _request(profile)
        for field in ("profile_sha256", "source_sha256"):
            with self.subTest(field=field):
                adapter = OpenAIShapedFakeAdapter(profile)
                setattr(adapter, field, "0" * 64)

                with self.assertRaisesRegex(ProviderProtocolError, "binding changed"):
                    execute_sealed_request(request, adapter)
                self.assertEqual(adapter.calls, 0)

    def test_credentials_and_tools_cannot_enter_provider_profile(self) -> None:
        for field, value, message in (
            ("api_key", "credential", "credential material"),
            ("access_token", "credential", "credential material"),
            ("tool_policy", "auto", "tools must be forbidden"),
        ):
            with self.subTest(field=field):
                profile = json.loads(_profile())
                profile["request_parameters"][field] = value
                if field == "tool_policy":
                    profile["request_parameters"]["tool_policy"] = value

                with self.assertRaisesRegex(ProviderProtocolError, message):
                    _request(canonical_bytes(profile))

    def test_profile_schema_and_endpoint_are_canonical_and_credential_free(
        self,
    ) -> None:
        profile = json.loads(_profile())
        pretty = json.dumps(profile, indent=2).encode()
        with self.assertRaisesRegex(ProviderProtocolError, "canonical JSON"):
            _request(pretty)

        profile["endpoint"] = "https://user:password@api.openai.example/v1/messages"
        with self.assertRaisesRegex(ProviderProtocolError, "credential-free HTTPS"):
            _request(canonical_bytes(profile))

        profile = json.loads(_profile())
        profile["idempotency"]["header"] = "authorization"
        with self.assertRaisesRegex(ProviderProtocolError, "idempotency contract"):
            _request(canonical_bytes(profile))

        with self.assertRaisesRegex(ProviderProtocolError, "canonical JSON"):
            SealedProviderRequest(
                phase="answer",
                schedule_index=0,
                attempt_number=1,
                profile=_profile(),
                prompt=b"prompt",
                output_schema=json.dumps(json.loads(SCHEMA), indent=2).encode(),
                idempotency_key="a11b-answer-0000-attempt-1",
                timeout_seconds=600,
            )

    def test_exchange_identity_schema_usage_and_tool_drift_fail_closed(self) -> None:
        cases: tuple[
            tuple[str, Callable[[AdapterExchange], AdapterExchange], str], ...
        ] = (
            (
                "idempotency",
                lambda item: replace(item, idempotency_key_sha256="0" * 64),
                "idempotency binding",
            ),
            (
                "model",
                lambda item: replace(item, returned_model="different-model"),
                "returned model",
            ),
            (
                "finish",
                lambda item: replace(item, finish_reason="length"),
                "finish reason",
            ),
            (
                "tools",
                lambda item: replace(item, tool_calls=1),
                "tools were called",
            ),
            (
                "status",
                lambda item: replace(item, http_status=429),
                "HTTP status",
            ),
            (
                "schema",
                lambda item: replace(
                    item,
                    normalized_answer=canonical_bytes({"wrong": "shape"}),
                ),
                "violates schema",
            ),
            (
                "usage",
                lambda item: replace(item, usage={**item.usage, "total": 999}),
                "does not reconcile",
            ),
            (
                "response-id",
                lambda item: replace(item, response_id=""),
                "response ID is invalid",
            ),
            (
                "latency",
                lambda item: replace(item, latency_ms=-1),
                "latency is invalid",
            ),
            (
                "raw-response",
                lambda item: replace(item, response_body=b'{"id":"a","id":"b"}'),
                "not strict JSON",
            ),
        )
        profile = _profile()
        request = _request(profile)
        for label, mutate, message in cases:
            with self.subTest(label=label):
                adapter = MutatingFakeAdapter(profile, mutate)

                with self.assertRaisesRegex(ProviderIntegrityError, message):
                    execute_sealed_request(request, adapter)
                self.assertEqual(adapter.calls, 1)

    def test_adapter_exception_is_terminally_indeterminate_and_never_retried(self) -> None:
        profile = _profile()
        adapter = RaisingFakeAdapter(profile)

        with self.assertRaisesRegex(
            ProviderIndeterminateError, "after invocation was authorized"
        ):
            execute_sealed_request(_request(profile), adapter)

        self.assertEqual(adapter.calls, 1)

    def test_process_control_exception_is_not_reclassified_as_transport(self) -> None:
        profile = _profile()
        adapter = InterruptingFakeAdapter(profile)

        with self.assertRaises(KeyboardInterrupt):
            execute_sealed_request(_request(profile), adapter)

        self.assertEqual(adapter.calls, 1)

    def test_definite_rate_limit_returns_canonical_retryable_failure(self) -> None:
        profile = _profile()
        adapter = RateLimitedFakeAdapter(profile)

        result = execute_sealed_request(_request(profile), adapter)

        self.assertEqual(result.outcome, "provider_failure")
        self.assertIsNone(result.answer)
        self.assertTrue(result.receipt["failure"]["retryable"])
        self.assertEqual(result.receipt["transport"]["http_status"], 429)
        self.assertEqual(result.receipt["usage"]["source"], "provider.error")
        self.assertEqual(adapter.calls, 1)

    def test_failure_retry_and_usage_classification_are_profile_bound(self) -> None:
        profile = _profile()
        cases: tuple[
            tuple[
                Callable[[AdapterFailureExchange], AdapterFailureExchange], str
            ],
            ...,
        ] = (
            (
                lambda item: replace(item, retryable=False),
                "retry classification changed",
            ),
            (
                lambda item: replace(
                    item,
                    usage={
                        "input": 1,
                        "cached": 0,
                        "output": 0,
                        "reasoning": 0,
                        "total": 1,
                        "complete": False,
                        "source": "provider.error",
                    },
                ),
                "must contain an unknown value",
            ),
        )
        for mutate, message in cases:
            with self.subTest(message=message):
                adapter = MutatingFailureAdapter(profile, mutate)
                with self.assertRaisesRegex(ProviderIntegrityError, message):
                    execute_sealed_request(_request(profile), adapter)
                self.assertEqual(adapter.calls, 1)

    def test_retained_exchange_replays_without_calling_adapter(self) -> None:
        profile = _profile()
        request = _request(profile)
        adapter = OpenAIShapedFakeAdapter(profile)
        exchange = adapter.invoke(request)
        expected_adapter = OpenAIShapedFakeAdapter(profile)
        result = execute_sealed_request(request, expected_adapter)

        verify_provider_result(request, exchange, result)

        self.assertEqual(adapter.calls, 1)
        self.assertEqual(expected_adapter.calls, 1)
        tampered = CanonicalProviderResult(
            outcome=result.outcome,
            answer=result.answer,
            receipt={**result.receipt, "provider": "tampered"},
        )
        with self.assertRaisesRegex(ProviderIntegrityError, "does not match"):
            verify_provider_result(request, exchange, tampered)

        tampered = CanonicalProviderResult(
            outcome="provider_failure",
            answer=result.answer,
            receipt=result.receipt,
        )
        with self.assertRaisesRegex(ProviderIntegrityError, "does not match"):
            verify_provider_result(request, exchange, tampered)


if __name__ == "__main__":
    unittest.main()
