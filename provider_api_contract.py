#!/usr/bin/env python3
"""Provider-neutral, fail-closed contract for one sealed direct-API request.

This module owns no credentials and implements no provider SDK. A trusted,
source-pinned adapter receives one already-sealed request and returns the exact
request/response bytes plus a normalized answer and usage record. The contract
calls the adapter once, never retries, and emits a canonical content-only
receipt after validating the exchange.
"""

from __future__ import annotations

import copy
import json
import math
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

import codex_harness
from a11_evidence_core import canonical_bytes, sha256


PROFILE_VERSION = "provider-api-profile-v1"
RESULT_RECEIPT_VERSION = "provider-api-result-receipt-v1"
_PROFILE_FIELDS = {
    "schema_version",
    "provider",
    "endpoint",
    "api_surface",
    "model",
    "adapter",
    "request_parameters",
    "usage_mapping",
    "accepted_finish_reasons",
    "retryable_http_statuses",
    "pricing_snapshot_sha256",
    "idempotency",
}
_ADAPTER_FIELDS = {
    "name",
    "version",
    "source_sha256",
    "dependencies",
}
_DEPENDENCY_FIELDS = {"name", "version", "sha256"}
_IDEMPOTENCY_FIELDS = {"supported", "header", "retry_policy"}
_USAGE_FIELDS = {
    "input",
    "cached",
    "output",
    "reasoning",
    "total",
    "complete",
    "source",
}
_DEPENDENCY_FILES = (
    "a11_evidence_core.py",
    "codex_harness.py",
    "provider_api_contract.py",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
_MAX_PROMPT_BYTES = 64 * 1024 * 1024
_MAX_SCHEMA_BYTES = 1024 * 1024
_MAX_REQUEST_BYTES = 64 * 1024 * 1024
_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_MAX_ANSWER_BYTES = 1024 * 1024
_MAX_TIMEOUT_SECONDS = 3600
_MAX_LATENCY_MS = 24 * 60 * 60 * 1000


class ProviderProtocolError(ValueError):
    """The sealed profile/request violated the provider-neutral contract."""


class ProviderIntegrityError(ValueError):
    """A returned provider exchange failed the registered acceptance rules."""


class ProviderIndeterminateError(RuntimeError):
    """The adapter raised after a provider call may have started; never auto-retry."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _parse_json(data: bytes, label: str) -> Any:
    try:
        return json.loads(
            data,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProviderProtocolError(f"{label} is not strict JSON") from exc


def _canonical_json(data: bytes, label: str) -> Any:
    value = _parse_json(data, label)
    if canonical_bytes(value) != data:
        raise ProviderProtocolError(f"{label} must use canonical JSON bytes")
    return value


def _finite_json(value: Any, label: str) -> None:
    if value is None or isinstance(value, (bool, str)) or type(value) is int:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ProviderProtocolError(f"{label} contains a non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _finite_json(item, label)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _finite_json(item, label)
        return
    raise ProviderProtocolError(f"{label} contains a non-JSON value")


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ProviderProtocolError(f"{label} must be a lowercase SHA-256")
    return value


def _secret_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    compact = normalized.replace("_", "")
    if compact in {
        "apikey",
        "authorization",
        "auth",
        "bearer",
        "credential",
        "password",
        "secret",
        "token",
        "accesstoken",
        "authtoken",
    }:
        return True
    return normalized.endswith(("_api_key", "_secret", "_password", "_token"))


def _reject_credentials(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _secret_key(key):
                raise ProviderProtocolError(
                    "provider profile must not contain credential material"
                )
            _reject_credentials(child)
    elif isinstance(value, list):
        for child in value:
            _reject_credentials(child)


def _profile(profile_bytes: bytes) -> dict[str, Any]:
    if not isinstance(profile_bytes, bytes) or not profile_bytes:
        raise ProviderProtocolError("provider profile bytes are invalid")
    value = _canonical_json(profile_bytes, "provider profile")
    if not isinstance(value, dict) or set(value) != _PROFILE_FIELDS:
        raise ProviderProtocolError("provider profile fields are invalid")
    _finite_json(value, "provider profile")
    _reject_credentials(value)
    if value.get("schema_version") != PROFILE_VERSION:
        raise ProviderProtocolError("provider profile version is invalid")
    provider = value.get("provider")
    if not isinstance(provider, str) or _PROVIDER.fullmatch(provider) is None:
        raise ProviderProtocolError("provider identifier is invalid")
    for field in ("api_surface", "model", "usage_mapping"):
        item = value.get(field)
        if not isinstance(item, str) or _IDENTIFIER.fullmatch(item) is None:
            raise ProviderProtocolError(f"provider {field} is invalid")

    endpoint = value.get("endpoint")
    if not isinstance(endpoint, str):
        raise ProviderProtocolError("provider endpoint is invalid")
    parsed = urllib.parse.urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderProtocolError("provider endpoint must be credential-free HTTPS")

    adapter = value.get("adapter")
    if not isinstance(adapter, dict) or set(adapter) != _ADAPTER_FIELDS:
        raise ProviderProtocolError("provider adapter fields are invalid")
    for field in ("name", "version"):
        item = adapter.get(field)
        if not isinstance(item, str) or _IDENTIFIER.fullmatch(item) is None:
            raise ProviderProtocolError(f"provider adapter {field} is invalid")
    _require_sha256(adapter.get("source_sha256"), "adapter source digest")
    dependencies = adapter.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise ProviderProtocolError("provider adapter dependencies are invalid")
    names: list[str] = []
    for dependency in dependencies:
        if not isinstance(dependency, dict) or set(dependency) != _DEPENDENCY_FIELDS:
            raise ProviderProtocolError("provider adapter dependency fields are invalid")
        for field in ("name", "version"):
            item = dependency.get(field)
            if not isinstance(item, str) or _IDENTIFIER.fullmatch(item) is None:
                raise ProviderProtocolError(
                    f"provider adapter dependency {field} is invalid"
                )
        _require_sha256(dependency.get("sha256"), "adapter dependency digest")
        names.append(dependency["name"])
    if names != sorted(names) or len(names) != len(set(names)):
        raise ProviderProtocolError("provider adapter dependencies must be uniquely sorted")

    parameters = value.get("request_parameters")
    if not isinstance(parameters, dict) or not parameters:
        raise ProviderProtocolError("provider request parameters are invalid")
    if parameters.get("tool_policy") != "forbidden":
        raise ProviderProtocolError("provider tools must be forbidden")
    max_output = parameters.get("max_output_tokens")
    if type(max_output) is not int or max_output <= 0:
        raise ProviderProtocolError("provider max output tokens is invalid")

    finish_reasons = value.get("accepted_finish_reasons")
    if (
        not isinstance(finish_reasons, list)
        or not finish_reasons
        or any(
            not isinstance(item, str) or _IDENTIFIER.fullmatch(item) is None
            for item in finish_reasons
        )
        or finish_reasons != sorted(set(finish_reasons))
    ):
        raise ProviderProtocolError("accepted finish reasons are invalid")
    retryable_statuses = value.get("retryable_http_statuses")
    if (
        not isinstance(retryable_statuses, list)
        or not retryable_statuses
        or any(type(item) is not int or not 400 <= item <= 599 for item in retryable_statuses)
        or retryable_statuses != sorted(set(retryable_statuses))
    ):
        raise ProviderProtocolError("retryable HTTP statuses are invalid")
    _require_sha256(
        value.get("pricing_snapshot_sha256"), "pricing snapshot digest"
    )
    idempotency = value.get("idempotency")
    if not isinstance(idempotency, dict) or set(idempotency) != _IDEMPOTENCY_FIELDS:
        raise ProviderProtocolError("idempotency profile fields are invalid")
    idempotency_header = idempotency.get("header")
    if (
        idempotency.get("supported") is not True
        or idempotency.get("retry_policy") != "same-key-only"
        or not isinstance(idempotency_header, str)
        or _IDENTIFIER.fullmatch(idempotency_header) is None
        or _secret_key(idempotency_header)
    ):
        raise ProviderProtocolError("provider idempotency contract is invalid")
    return value


@dataclass(frozen=True)
class SealedProviderRequest:
    phase: str
    schedule_index: int
    attempt_number: int
    profile: bytes
    prompt: bytes
    output_schema: bytes
    idempotency_key: str
    timeout_seconds: int

    def __post_init__(self) -> None:
        if self.phase not in {"answer", "panel"}:
            raise ProviderProtocolError("provider request phase is invalid")
        if type(self.schedule_index) is not int or self.schedule_index < 0:
            raise ProviderProtocolError("provider schedule index is invalid")
        if type(self.attempt_number) is not int or self.attempt_number <= 0:
            raise ProviderProtocolError("provider attempt number is invalid")
        if (
            not isinstance(self.prompt, bytes)
            or not self.prompt
            or len(self.prompt) > _MAX_PROMPT_BYTES
        ):
            raise ProviderProtocolError("provider prompt bytes are invalid")
        if (
            not isinstance(self.output_schema, bytes)
            or not self.output_schema
            or len(self.output_schema) > _MAX_SCHEMA_BYTES
        ):
            raise ProviderProtocolError("provider output schema bytes are invalid")
        schema = _canonical_json(self.output_schema, "provider output schema")
        if not isinstance(schema, dict):
            raise ProviderProtocolError("provider output schema must be an object")
        _profile(self.profile)
        if (
            not isinstance(self.idempotency_key, str)
            or _IDEMPOTENCY_KEY.fullmatch(self.idempotency_key) is None
        ):
            raise ProviderProtocolError("provider idempotency key is invalid")
        if (
            type(self.timeout_seconds) is not int
            or not 1 <= self.timeout_seconds <= _MAX_TIMEOUT_SECONDS
        ):
            raise ProviderProtocolError("provider timeout is invalid")

    @property
    def profile_sha256(self) -> str:
        return sha256(self.profile)

    def commitment(self) -> str:
        return sha256(
            canonical_bytes(
                {
                    "phase": self.phase,
                    "schedule_index": self.schedule_index,
                    "attempt_number": self.attempt_number,
                    "profile_sha256": self.profile_sha256,
                    "prompt_sha256": sha256(self.prompt),
                    "prompt_bytes": len(self.prompt),
                    "output_schema_sha256": sha256(self.output_schema),
                    "output_schema_bytes": len(self.output_schema),
                    "idempotency_key_sha256": sha256(
                        self.idempotency_key.encode("utf-8")
                    ),
                    "timeout_seconds": self.timeout_seconds,
                }
            )
        )


@dataclass(frozen=True)
class AdapterExchange:
    profile_sha256: str
    idempotency_key_sha256: str
    request_body: bytes
    response_body: bytes
    normalized_answer: bytes
    http_status: int
    response_id: str
    provider_request_id: str
    returned_model: str
    finish_reason: str
    tool_calls: int
    latency_ms: int
    usage: Mapping[str, Any]


@dataclass(frozen=True)
class AdapterFailureExchange:
    profile_sha256: str
    idempotency_key_sha256: str
    request_body: bytes
    response_body: bytes
    http_status: int
    response_id: str | None
    provider_request_id: str
    error_code: str
    retryable: bool
    latency_ms: int
    usage: Mapping[str, Any]


@dataclass(frozen=True)
class CanonicalProviderResult:
    outcome: str
    answer: bytes | None
    receipt: dict[str, Any]


class ProviderAdapter(Protocol):
    profile_sha256: str
    source_sha256: str

    def invoke(
        self, request: SealedProviderRequest
    ) -> AdapterExchange | AdapterFailureExchange: ...


def _usage(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _USAGE_FIELDS:
        raise ProviderIntegrityError("provider usage fields are invalid")
    usage = dict(value)
    for field in ("input", "cached", "output", "reasoning", "total"):
        amount = usage.get(field)
        if type(amount) is not int or amount < 0:
            raise ProviderIntegrityError("provider usage values are invalid")
    if usage.get("complete") is not True or usage.get("source") != "provider.api":
        raise ProviderIntegrityError("provider usage completeness is invalid")
    if (
        usage["cached"] > usage["input"]
        or usage["reasoning"] > usage["output"]
        or usage["total"] != usage["input"] + usage["output"]
    ):
        raise ProviderIntegrityError("provider usage does not reconcile")
    return usage


def _failure_usage(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _USAGE_FIELDS:
        raise ProviderIntegrityError("provider failure usage fields are invalid")
    usage = dict(value)
    for field in ("input", "cached", "output", "reasoning", "total"):
        amount = usage.get(field)
        if amount is not None and (type(amount) is not int or amount < 0):
            raise ProviderIntegrityError("provider failure usage values are invalid")
    if usage.get("source") != "provider.error" or type(usage.get("complete")) is not bool:
        raise ProviderIntegrityError("provider failure usage completeness is invalid")
    values = [usage[field] for field in ("input", "cached", "output", "reasoning", "total")]
    if usage["complete"]:
        if any(value is None for value in values):
            raise ProviderIntegrityError("complete provider failure usage is missing values")
        assert all(isinstance(value, int) for value in values)
        if (
            usage["cached"] > usage["input"]
            or usage["reasoning"] > usage["output"]
            or usage["total"] != usage["input"] + usage["output"]
        ):
            raise ProviderIntegrityError("provider failure usage does not reconcile")
    elif all(value is not None for value in values):
        raise ProviderIntegrityError(
            "incomplete provider failure usage must contain an unknown value"
        )
    return usage


def _dependency_receipts() -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parent
    receipts = []
    for relative in sorted(_DEPENDENCY_FILES):
        payload = (root / relative).read_bytes()
        receipts.append(
            {"path": relative, "sha256": sha256(payload), "bytes": len(payload)}
        )
    return receipts


def _request_receipt(
    request: SealedProviderRequest, profile: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "phase": request.phase,
        "schedule_index": request.schedule_index,
        "attempt_number": request.attempt_number,
        "commitment": request.commitment(),
        "prompt_sha256": sha256(request.prompt),
        "prompt_bytes": len(request.prompt),
        "output_schema_sha256": sha256(request.output_schema),
        "output_schema_bytes": len(request.output_schema),
        "request_parameters_sha256": sha256(
            canonical_bytes(profile["request_parameters"])
        ),
        "idempotency_key_sha256": sha256(
            request.idempotency_key.encode("utf-8")
        ),
        "timeout_seconds": request.timeout_seconds,
    }


def _base_receipt(
    request: SealedProviderRequest,
    profile: dict[str, Any],
    *,
    request_body: bytes,
    response_body: bytes,
    http_status: int,
    response_id: str | None,
    provider_request_id: str,
    latency_ms: int,
    usage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": RESULT_RECEIPT_VERSION,
        "provider": profile["provider"],
        "api_surface": profile["api_surface"],
        "endpoint": profile["endpoint"],
        "model": profile["model"],
        "profile_sha256": request.profile_sha256,
        "request": _request_receipt(request, profile),
        "transport": {
            "adapter": copy.deepcopy(profile["adapter"]),
            "request_body_sha256": sha256(request_body),
            "request_body_bytes": len(request_body),
            "response_body_sha256": sha256(response_body),
            "response_body_bytes": len(response_body),
            "http_status": http_status,
            "response_id": response_id,
            "provider_request_id": provider_request_id,
            "latency_ms": latency_ms,
        },
        "usage": usage,
        "usage_mapping": profile["usage_mapping"],
        "pricing_snapshot_sha256": profile["pricing_snapshot_sha256"],
        "contract_dependencies": _dependency_receipts(),
        "adapter_invocations": 1,
    }


def _compile_result(
    request: SealedProviderRequest,
    profile: dict[str, Any],
    exchange: AdapterExchange,
) -> CanonicalProviderResult:
    if not isinstance(exchange, AdapterExchange):
        raise ProviderIntegrityError("provider adapter returned an invalid exchange")
    expected_idempotency = sha256(request.idempotency_key.encode("utf-8"))
    if exchange.profile_sha256 != request.profile_sha256:
        raise ProviderIntegrityError("provider exchange profile binding changed")
    if exchange.idempotency_key_sha256 != expected_idempotency:
        raise ProviderIntegrityError("provider exchange idempotency binding changed")
    for payload, maximum, label in (
        (exchange.request_body, _MAX_REQUEST_BYTES, "provider request body"),
        (exchange.response_body, _MAX_RESPONSE_BYTES, "provider response body"),
        (exchange.normalized_answer, _MAX_ANSWER_BYTES, "provider normalized answer"),
    ):
        if not isinstance(payload, bytes) or not payload or len(payload) > maximum:
            raise ProviderIntegrityError(f"{label} is invalid")
    try:
        _parse_json(exchange.request_body, "provider request body")
        _parse_json(exchange.response_body, "provider response body")
        answer = _canonical_json(
            exchange.normalized_answer, "provider normalized answer"
        )
    except ProviderProtocolError as exc:
        raise ProviderIntegrityError(str(exc)) from exc
    schema = _canonical_json(request.output_schema, "provider output schema")
    if not isinstance(answer, dict) or not codex_harness._matches_json_schema(
        answer, schema
    ):
        raise ProviderIntegrityError("provider normalized answer violates schema")
    if type(exchange.http_status) is not int or not 200 <= exchange.http_status < 300:
        raise ProviderIntegrityError("provider HTTP status is not successful")
    for value, label in (
        (exchange.response_id, "provider response ID"),
        (exchange.provider_request_id, "provider request ID"),
    ):
        if not isinstance(value, str) or _OPAQUE_ID.fullmatch(value) is None:
            raise ProviderIntegrityError(f"{label} is invalid")
    if exchange.returned_model != profile["model"]:
        raise ProviderIntegrityError("provider returned model changed")
    if exchange.finish_reason not in profile["accepted_finish_reasons"]:
        raise ProviderIntegrityError("provider finish reason is not accepted")
    if type(exchange.tool_calls) is not int or exchange.tool_calls != 0:
        raise ProviderIntegrityError("provider tools were called")
    if (
        type(exchange.latency_ms) is not int
        or not 0 <= exchange.latency_ms <= _MAX_LATENCY_MS
    ):
        raise ProviderIntegrityError("provider latency is invalid")
    usage = _usage(exchange.usage)
    receipt = _base_receipt(
        request,
        profile,
        request_body=exchange.request_body,
        response_body=exchange.response_body,
        http_status=exchange.http_status,
        response_id=exchange.response_id,
        provider_request_id=exchange.provider_request_id,
        latency_ms=exchange.latency_ms,
        usage=usage,
    )
    receipt["outcome"] = "accepted"
    receipt["transport"].update(
        {
            "returned_model": exchange.returned_model,
            "finish_reason": exchange.finish_reason,
            "tool_calls": exchange.tool_calls,
        }
    )
    receipt["answer"] = {
        "sha256": sha256(exchange.normalized_answer),
        "bytes": len(exchange.normalized_answer),
    }
    return CanonicalProviderResult(
        outcome="accepted",
        answer=bytes(exchange.normalized_answer),
        receipt=receipt,
    )


def _compile_failure(
    request: SealedProviderRequest,
    profile: dict[str, Any],
    exchange: AdapterFailureExchange,
) -> CanonicalProviderResult:
    if not isinstance(exchange, AdapterFailureExchange):
        raise ProviderIntegrityError("provider adapter returned an invalid failure")
    expected_idempotency = sha256(request.idempotency_key.encode("utf-8"))
    if exchange.profile_sha256 != request.profile_sha256:
        raise ProviderIntegrityError("provider failure profile binding changed")
    if exchange.idempotency_key_sha256 != expected_idempotency:
        raise ProviderIntegrityError("provider failure idempotency binding changed")
    for payload, maximum, label in (
        (exchange.request_body, _MAX_REQUEST_BYTES, "provider request body"),
        (exchange.response_body, _MAX_RESPONSE_BYTES, "provider response body"),
    ):
        if not isinstance(payload, bytes) or not payload or len(payload) > maximum:
            raise ProviderIntegrityError(f"{label} is invalid")
    try:
        _parse_json(exchange.request_body, "provider request body")
        _parse_json(exchange.response_body, "provider response body")
    except ProviderProtocolError as exc:
        raise ProviderIntegrityError(str(exc)) from exc
    if type(exchange.http_status) is not int or not 400 <= exchange.http_status <= 599:
        raise ProviderIntegrityError("provider failure HTTP status is invalid")
    if exchange.response_id is not None and (
        not isinstance(exchange.response_id, str)
        or _OPAQUE_ID.fullmatch(exchange.response_id) is None
    ):
        raise ProviderIntegrityError("provider failure response ID is invalid")
    if (
        not isinstance(exchange.provider_request_id, str)
        or _OPAQUE_ID.fullmatch(exchange.provider_request_id) is None
    ):
        raise ProviderIntegrityError("provider failure request ID is invalid")
    if (
        not isinstance(exchange.error_code, str)
        or _IDENTIFIER.fullmatch(exchange.error_code) is None
        or type(exchange.retryable) is not bool
    ):
        raise ProviderIntegrityError("provider failure classification is invalid")
    registered_retryable = exchange.http_status in profile["retryable_http_statuses"]
    if exchange.retryable != registered_retryable:
        raise ProviderIntegrityError("provider failure retry classification changed")
    if (
        type(exchange.latency_ms) is not int
        or not 0 <= exchange.latency_ms <= _MAX_LATENCY_MS
    ):
        raise ProviderIntegrityError("provider failure latency is invalid")
    usage = _failure_usage(exchange.usage)
    receipt = _base_receipt(
        request,
        profile,
        request_body=exchange.request_body,
        response_body=exchange.response_body,
        http_status=exchange.http_status,
        response_id=exchange.response_id,
        provider_request_id=exchange.provider_request_id,
        latency_ms=exchange.latency_ms,
        usage=usage,
    )
    receipt["outcome"] = "provider_failure"
    receipt["failure"] = {
        "error_code": exchange.error_code,
        "retryable": exchange.retryable,
        "retry_policy": profile["idempotency"]["retry_policy"],
    }
    return CanonicalProviderResult(
        outcome="provider_failure",
        answer=None,
        receipt=receipt,
    )


def execute_sealed_request(
    request: SealedProviderRequest, adapter: ProviderAdapter
) -> CanonicalProviderResult:
    """Invoke exactly one pinned adapter and validate its complete exchange."""

    if not isinstance(request, SealedProviderRequest):
        raise ProviderProtocolError("sealed provider request is invalid")
    profile = _profile(request.profile)
    if getattr(adapter, "profile_sha256", None) != request.profile_sha256:
        raise ProviderProtocolError("provider adapter profile binding changed")
    if getattr(adapter, "source_sha256", None) != profile["adapter"]["source_sha256"]:
        raise ProviderProtocolError("provider adapter source binding changed")
    try:
        exchange = adapter.invoke(request)
    except Exception as exc:
        raise ProviderIndeterminateError(
            "provider adapter raised after invocation was authorized"
        ) from exc
    if isinstance(exchange, AdapterFailureExchange):
        return _compile_failure(request, profile, exchange)
    return _compile_result(request, profile, exchange)


def verify_provider_result(
    request: SealedProviderRequest,
    exchange: AdapterExchange | AdapterFailureExchange,
    result: CanonicalProviderResult,
) -> None:
    """Recompute a retained exchange without invoking its provider adapter."""

    if not isinstance(request, SealedProviderRequest):
        raise ProviderProtocolError("sealed provider request is invalid")
    if not isinstance(result, CanonicalProviderResult):
        raise ProviderIntegrityError("canonical provider result is invalid")
    profile = _profile(request.profile)
    expected = (
        _compile_failure(request, profile, exchange)
        if isinstance(exchange, AdapterFailureExchange)
        else _compile_result(request, profile, exchange)
    )
    if (
        result.outcome != expected.outcome
        or result.answer != expected.answer
        or canonical_bytes(result.receipt) != canonical_bytes(expected.receipt)
    ):
        raise ProviderIntegrityError(
            "canonical provider result does not match the retained exchange"
        )
