#!/usr/bin/env python3
"""Trusted, crash-conservative executor for witnessed experiment calls.

The public caller supplies only a run ID and the last independently retained
witness head.  Every model-facing field is loaded from the sealed invocation
schedule owned by this process.  The driver writes raw capture files; outcome,
retry class, token usage, and artifact commitments are derived here.

Exactly-once external process execution cannot be proven across an arbitrary
host crash without provider idempotency.  This module therefore guarantees one
durable reservation and at-most-one spawn: any spawn intent lacking a durable
capture becomes terminally indeterminate and is never respawned.
"""

from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence

import codex_harness
import experiment_witness as witness


EXECUTOR_SCHEMA_VERSION = "experiment-executor-v1"
CAPTURE_FILE_NAMES = ("events.jsonl", "answer.json", "stderr.log")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_MODEL_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class ExecutorProtocolError(ValueError):
    """The caller or sealed configuration violated the executor protocol."""


class ExecutorIntegrityError(ValueError):
    """Durable executor state or an artifact failed verification."""


class ExecutorIndeterminateError(RuntimeError):
    """A prior spawn may have occurred without a durable terminal capture."""

    def __init__(
        self, message: str, *, result: ExecutionBundle | None = None
    ) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class SealedInvocation:
    phase: Literal["answer", "panel"]
    schedule_index: int
    prompt: bytes
    output_schema: bytes
    model: str
    reasoning_effort: str
    runtime_path: str
    runtime_sha256: str
    timeout_seconds: int

    def __post_init__(self) -> None:
        if self.phase not in witness.PHASES:
            raise ExecutorProtocolError("sealed invocation phase is invalid")
        if type(self.schedule_index) is not int or self.schedule_index < 0:
            raise ExecutorProtocolError("sealed invocation index is invalid")
        if not isinstance(self.prompt, bytes) or not self.prompt:
            raise ExecutorProtocolError("sealed invocation prompt is invalid")
        if not isinstance(self.output_schema, bytes) or not self.output_schema:
            raise ExecutorProtocolError("sealed output schema is invalid")
        try:
            schema = json.loads(
                self.output_schema, object_pairs_hook=_unique_json_object
            )
        except (UnicodeDecodeError, ValueError) as exc:
            raise ExecutorProtocolError("sealed output schema is invalid") from exc
        if not isinstance(schema, dict):
            raise ExecutorProtocolError("sealed output schema is invalid")
        _require_hex64(self.runtime_sha256, "runtime digest")
        if (
            not isinstance(self.model, str)
            or _MODEL_IDENTIFIER.fullmatch(self.model) is None
        ):
            raise ExecutorProtocolError("sealed invocation model is invalid")
        if self.reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            raise ExecutorProtocolError("sealed invocation reasoning is invalid")
        if not isinstance(self.runtime_path, str) or not Path(
            self.runtime_path
        ).is_absolute():
            raise ExecutorProtocolError("sealed invocation runtime path is invalid")
        if type(self.timeout_seconds) is not int or self.timeout_seconds <= 0:
            raise ExecutorProtocolError("sealed invocation timeout is invalid")

    def commitment_payload(self) -> bytes:
        return witness.canonical_json_bytes(
            {
                "phase": self.phase,
                "schedule_index": self.schedule_index,
                "prompt_base64": base64.b64encode(self.prompt).decode("ascii"),
                "output_schema_base64": base64.b64encode(
                    self.output_schema
                ).decode("ascii"),
                "output_schema_sha256": witness.sha256_bytes(self.output_schema),
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "runtime_path": self.runtime_path,
                "runtime_sha256": self.runtime_sha256,
                "timeout_seconds": self.timeout_seconds,
            }
        )

    def call_commitment(self, secret_key: bytes) -> str:
        return witness.keyed_commitment(
            secret_key,
            domain="executor-call",
            payload=self.commitment_payload(),
        )


@dataclass(frozen=True)
class DriverTermination:
    exit_code: int
    timed_out: bool
    runtime_sha256: str

    def __post_init__(self) -> None:
        if type(self.exit_code) is not int:
            raise ExecutorProtocolError("driver exit code is invalid")
        if type(self.timed_out) is not bool:
            raise ExecutorProtocolError("driver timeout marker is invalid")
        _require_hex64(self.runtime_sha256, "driver runtime digest")


class ModelDriver(Protocol):
    def invoke(
        self, invocation: SealedInvocation, capture_dir: Path
    ) -> DriverTermination: ...


@dataclass(frozen=True)
class DerivedTerminal:
    outcome: Literal[
        "accepted", "provider_failure", "contaminated", "indeterminate"
    ]
    token_usage: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class ExecutionBundle:
    run_id: str
    request_head: str
    witness_head: str
    outcome: str
    token_usage: dict[str, Any]
    artifact_ref: str
    artifact_root_commitment: str
    opened_receipt: dict[str, Any]
    closed_receipt: dict[str, Any]
    reason: str


def _require_hex64(value: Any, label: str) -> str:
    if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
        raise ExecutorProtocolError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _complete_usage(events: bytes) -> dict[str, Any] | None:
    completed: list[dict[str, Any]] = []
    try:
        text = events.decode("utf-8", errors="strict")
        if events and not events.endswith(b"\n"):
            return None
        for line in text.splitlines():
            event = json.loads(line, object_pairs_hook=_unique_json_object)
            if not isinstance(event, dict):
                return None
            if event.get("type") == "turn.completed":
                completed.append(event)
    except (UnicodeDecodeError, ValueError):
        return None
    if len(completed) != 1:
        return None
    raw = completed[0].get("usage")
    if not isinstance(raw, dict):
        return None
    names = {
        "input": "input_tokens",
        "cached": "cached_input_tokens",
        "output": "output_tokens",
        "reasoning": "reasoning_output_tokens",
        "total": "total_tokens",
    }
    values: dict[str, int] = {}
    for target, source in names.items():
        amount = raw.get(source)
        if type(amount) is not int or amount < 0:
            return None
        values[target] = amount
    if (
        values["cached"] > values["input"]
        or values["reasoning"] > values["output"]
        or values["total"] != values["input"] + values["output"]
    ):
        return None
    return {**values, "complete": True, "source": "turn.completed"}


def _unknown_usage(source: str) -> dict[str, Any]:
    return {
        "input": None,
        "cached": None,
        "output": None,
        "reasoning": None,
        "total": None,
        "complete": False,
        "source": source,
    }


class CaptureDecoder:
    """Derive terminal semantics only from a frozen raw-capture envelope."""

    def decode(
        self,
        capture: Mapping[str, Any],
        *,
        expected_runtime_sha256: str,
        output_schema: bytes,
    ) -> DerivedTerminal:
        if not isinstance(capture, Mapping) or set(capture) != {
            "kind",
            "schema_version",
            "termination",
            "pre_spawn_runtime_sha256",
            "files_base64",
            "integrity_errors",
        }:
            raise ExecutorIntegrityError("capture envelope fields changed")
        if capture.get("kind") != "experiment_executor_capture" or capture.get(
            "schema_version"
        ) != EXECUTOR_SCHEMA_VERSION:
            raise ExecutorIntegrityError("capture envelope identity changed")
        if capture.get("pre_spawn_runtime_sha256") != expected_runtime_sha256:
            raise ExecutorIntegrityError("capture pre-spawn runtime binding changed")
        termination = capture.get("termination")
        if not isinstance(termination, Mapping):
            raise ExecutorIntegrityError("capture termination is invalid")
        try:
            validated_termination = DriverTermination(
                exit_code=termination["exit_code"],
                timed_out=termination["timed_out"],
                runtime_sha256=termination["runtime_sha256"],
            )
        except (KeyError, ExecutorProtocolError) as exc:
            raise ExecutorIntegrityError("capture termination is invalid") from exc
        if set(termination) != {"exit_code", "timed_out", "runtime_sha256"}:
            raise ExecutorIntegrityError("capture termination fields changed")
        raw_files = capture.get("files_base64")
        errors = capture.get("integrity_errors")
        if not isinstance(raw_files, Mapping) or not isinstance(errors, list) or any(
            not isinstance(item, str) for item in errors
        ):
            raise ExecutorIntegrityError("capture inventory is invalid")
        files: dict[str, bytes] = {}
        try:
            for name, encoded in raw_files.items():
                if name not in CAPTURE_FILE_NAMES or not isinstance(encoded, str):
                    raise ExecutorIntegrityError("capture file inventory changed")
                files[name] = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ExecutorIntegrityError("capture file encoding changed") from exc

        events = files.get("events.jsonl", b"")
        usage = _complete_usage(events)
        with tempfile.TemporaryDirectory(prefix="experiment-executor-audit-") as tmp:
            event_path = Path(tmp) / "events.jsonl"
            if "events.jsonl" in files:
                event_path.write_bytes(events)
            audit = codex_harness.audit_event_log(event_path)
        answer_valid = False
        if "answer.json" in files:
            try:
                answer = json.loads(
                    files["answer.json"], object_pairs_hook=_unique_json_object
                )
                schema = json.loads(
                    output_schema, object_pairs_hook=_unique_json_object
                )
                answer_valid = (
                    isinstance(schema, dict)
                    and codex_harness._matches_json_schema(answer, schema)
                )
            except (UnicodeDecodeError, ValueError):
                answer_valid = False
        stderr_empty = files.get("stderr.log", b"") == b""
        required_capture_files_present = {
            "events.jsonl",
            "stderr.log",
        }.issubset(files)
        runtime_matches = (
            validated_termination.runtime_sha256 == expected_runtime_sha256
        )
        event_sequence = audit.get("event_type_sequence")
        allowed_clean_events = {
            "thread_started",
            "turn_started",
            "item_started",
            "item_completed",
            "turn_completed",
        }
        clean_terminal_shape = (
            isinstance(event_sequence, list)
            and len(event_sequence) >= 3
            and audit.get("thread_started_count") == 1
            and audit.get("turn_started_count") == 1
            and audit.get("turn_completed_count") == 1
            and audit.get("turn_failed_count") == 0
            and audit.get("error_event_count") == 0
            and event_sequence[0] == "thread_started"
            and event_sequence[-1] == "turn_completed"
            and all(event in allowed_clean_events for event in event_sequence)
        )

        if (
            validated_termination.exit_code == 0
            and not validated_termination.timed_out
            and runtime_matches
            and stderr_empty
            and required_capture_files_present
            and not errors
            and audit.get("contaminated") is False
            and clean_terminal_shape
            and answer_valid
            and usage is not None
        ):
            return DerivedTerminal(
                outcome="accepted",
                token_usage=usage,
                reason="accepted_complete_capture",
            )
        if (
            validated_termination.exit_code != 0
            and not validated_termination.timed_out
            and runtime_matches
            and stderr_empty
            and required_capture_files_present
            and not errors
            and "answer.json" not in files
            and codex_harness.is_retryable_incomplete_packet_audit(audit)
        ):
            return DerivedTerminal(
                outcome="provider_failure",
                token_usage=_unknown_usage("provider.error"),
                reason="registered_answerless_provider_failure",
            )
        return DerivedTerminal(
            outcome="contaminated",
            token_usage=usage if usage is not None else _unknown_usage("unavailable"),
            reason="capture_failed_registered_acceptance_rules",
        )


class ExperimentExecutor:
    """One trusted execution seam for a sealed witnessed schedule."""

    def __init__(
        self,
        root: Path,
        *,
        ledger: witness.WitnessLedger,
        invocations: Sequence[SealedInvocation],
        commitment_key: bytes,
        driver: ModelDriver,
        checkpoint: Callable[[str], None] | None = None,
    ) -> None:
        self.root = Path(os.path.abspath(root))
        self.ledger = ledger
        self.invocations = tuple(invocations)
        self.commitment_key = commitment_key
        self.driver = driver
        self.checkpoint = checkpoint or (lambda _boundary: None)
        self.decoder = CaptureDecoder()
        self.attempts_dir = self.root / "attempts"
        self.lock_path = self.root / ".lock"
        self._validate_sealed_schedule()
        self._initialize_storage()

    def _validate_sealed_schedule(self) -> None:
        if len(self.invocations) != len(self.ledger.schedule):
            raise ExecutorProtocolError("sealed invocation schedule length changed")
        runtimes: dict[str, str] = {}
        for invocation, item in zip(self.invocations, self.ledger.schedule, strict=True):
            if (
                invocation.phase != item.phase
                or invocation.schedule_index != item.schedule_index
                or invocation.call_commitment(self.commitment_key)
                != item.call_commitment
            ):
                raise ExecutorProtocolError(
                    "sealed invocation differs from the witnessed schedule"
                )
            prior_digest = runtimes.setdefault(
                invocation.runtime_path,
                invocation.runtime_sha256,
            )
            if prior_digest != invocation.runtime_sha256:
                raise ExecutorProtocolError(
                    "sealed invocations disagree about one runtime digest"
                )
        for runtime_path, runtime_sha256 in runtimes.items():
            if self._runtime_sha256(runtime_path) != runtime_sha256:
                raise ExecutorIntegrityError("sealed runtime digest changed")

    @staticmethod
    def _runtime_sha256(runtime_path: str) -> str:
        path = Path(runtime_path)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
            try:
                status = os.fstat(descriptor)
                path_status = path.lstat()
                if (
                    not stat.S_ISREG(status.st_mode)
                    or status.st_uid != os.geteuid()
                    or stat.S_IMODE(status.st_mode) & 0o022
                    or not stat.S_IMODE(status.st_mode) & 0o100
                    or (status.st_dev, status.st_ino)
                    != (path_status.st_dev, path_status.st_ino)
                ):
                    raise ExecutorIntegrityError(
                        "sealed runtime must be executor-owned and non-writable"
                    )
                digest = hashlib.sha256()
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise ExecutorIntegrityError("sealed runtime is unavailable") from exc
        return digest.hexdigest()

    @staticmethod
    def _require_private_directory(path: Path, label: str) -> None:
        status = path.lstat()
        if (
            not stat.S_ISDIR(status.st_mode)
            or status.st_uid != os.geteuid()
            or stat.S_IMODE(status.st_mode) != 0o700
        ):
            raise ExecutorIntegrityError(f"{label} must be private and executor-owned")

    def _initialize_storage(self) -> None:
        if self.root.is_symlink():
            raise ExecutorIntegrityError("executor root is a symlink")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._require_private_directory(self.root, "executor root")
        self.attempts_dir.mkdir(exist_ok=True, mode=0o700)
        self._require_private_directory(self.attempts_dir, "executor attempts")
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.lock_path, flags, 0o600)
        except OSError as exc:
            raise ExecutorIntegrityError("executor lock is unavailable") from exc
        try:
            status = os.fstat(descriptor)
            path_status = self.lock_path.lstat()
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != os.geteuid()
                or stat.S_IMODE(status.st_mode) != 0o600
                or (status.st_dev, status.st_ino)
                != (path_status.st_dev, path_status.st_ino)
            ):
                raise ExecutorIntegrityError(
                    "executor lock must be private and executor-owned"
                )
        finally:
            os.close(descriptor)

    @contextmanager
    def _locked(self):
        flags = os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.lock_path, flags)
        try:
            status = os.fstat(descriptor)
            path_status = self.lock_path.lstat()
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != os.geteuid()
                or stat.S_IMODE(status.st_mode) != 0o600
                or (status.st_dev, status.st_ino)
                != (path_status.st_dev, path_status.st_ino)
            ):
                raise ExecutorIntegrityError("executor lock changed")
            with os.fdopen(descriptor, "r+b", closefd=False) as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                yield
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("executor durable write made no progress")
            offset += written

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _publish_bytes(cls, path: Path, payload: bytes) -> None:
        temp = path.parent / f".tmp-{path.name}-{uuid.uuid4().hex}"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(temp, flags, 0o400)
            try:
                cls._write_all(descriptor, payload)
                os.fsync(descriptor)
                os.fchmod(descriptor, 0o400)
            finally:
                os.close(descriptor)
            os.link(temp, path, follow_symlinks=False)
            os.unlink(temp)
            cls._fsync_dir(path.parent)
        except BaseException:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @classmethod
    def _publish_marker(cls, path: Path, value: Mapping[str, Any]) -> None:
        cls._publish_bytes(path, witness.canonical_json_bytes(value) + b"\n")

    @staticmethod
    def _read_canonical(path: Path) -> dict[str, Any]:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
            try:
                status = os.fstat(descriptor)
                path_status = path.lstat()
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                raw = b"".join(chunks)
            finally:
                os.close(descriptor)
            value = json.loads(raw, object_pairs_hook=_unique_json_object)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ExecutorIntegrityError(f"executor marker is invalid: {path.name}") from exc
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_uid != os.geteuid()
            or stat.S_IMODE(status.st_mode) != 0o400
            or (status.st_dev, status.st_ino)
            != (path_status.st_dev, path_status.st_ino)
            or not isinstance(value, dict)
            or raw != witness.canonical_json_bytes(value) + b"\n"
        ):
            raise ExecutorIntegrityError(f"executor marker changed: {path.name}")
        return value

    def _attempt_id(self, descriptor: witness.CallDescriptor) -> str:
        return witness.sha256_bytes(
            witness.canonical_json_bytes(
                {
                    "run_id": self.ledger.run_id,
                    "phase": descriptor.phase,
                    "schedule_index": descriptor.schedule_index,
                    "attempt_number": descriptor.attempt_number,
                }
            )
        )

    def _attempt_dir(self, descriptor: witness.CallDescriptor) -> Path:
        path = self.attempts_dir / self._attempt_id(descriptor)
        path.mkdir(exist_ok=True, mode=0o700)
        self._require_private_directory(path, "executor attempt")
        return path

    def _descriptor_for_status(
        self, status: Mapping[str, Any]
    ) -> tuple[witness.CallDescriptor, SealedInvocation]:
        position = status.get("schedule_position")
        attempt_number = status.get("next_attempt_number")
        if type(position) is not int or not 0 <= position < len(self.invocations):
            raise ExecutorProtocolError("witness schedule position is invalid")
        if type(attempt_number) is not int or attempt_number < 1:
            raise ExecutorProtocolError("witness attempt number is invalid")
        invocation = self.invocations[position]
        item = self.ledger.schedule[position]
        descriptor = witness.CallDescriptor(
            phase=item.phase,
            schedule_index=item.schedule_index,
            attempt_number=attempt_number,
            call_commitment=item.call_commitment,
        )
        return descriptor, invocation

    def _invocation_for_descriptor(
        self, descriptor: witness.CallDescriptor
    ) -> SealedInvocation:
        matches = [
            invocation
            for invocation in self.invocations
            if invocation.phase == descriptor.phase
            and invocation.schedule_index == descriptor.schedule_index
        ]
        if len(matches) != 1:
            raise ExecutorIntegrityError("opened invocation is not uniquely sealed")
        if matches[0].call_commitment(self.commitment_key) != descriptor.call_commitment:
            raise ExecutorIntegrityError("opened invocation commitment changed")
        return matches[0]

    def _expected_spawn_marker(
        self,
        *,
        request_head: str,
        opened_receipt: Mapping[str, Any],
        invocation: SealedInvocation,
    ) -> dict[str, Any]:
        return {
            "kind": "experiment_executor_spawn_intent",
            "schema_version": EXECUTOR_SCHEMA_VERSION,
            "run_id": self.ledger.run_id,
            "request_head": request_head,
            "opened_receipt_sha256": witness.receipt_sha256(opened_receipt),
            "runtime_sha256": invocation.runtime_sha256,
        }

    def _opened_marker(
        self,
        *,
        request_head: str,
        descriptor: witness.CallDescriptor,
        opened_receipt: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "kind": "experiment_executor_opened",
            "schema_version": EXECUTOR_SCHEMA_VERSION,
            "run_id": self.ledger.run_id,
            "request_head": request_head,
            "descriptor": asdict(descriptor),
            "opened_receipt": opened_receipt,
        }

    def _find_attempt_for_request(self, request_head: str) -> Path | None:
        found: list[Path] = []
        for path in sorted(self.attempts_dir.iterdir(), key=lambda item: item.name):
            if path.is_symlink() or not path.is_dir():
                raise ExecutorIntegrityError("executor attempt inventory changed")
            self._require_private_directory(path, "executor attempt")
            opened_path = path / "opened.json"
            if opened_path.exists():
                opened = self._read_canonical(opened_path)
                if opened.get("request_head") == request_head:
                    found.append(path)
        if len(found) > 1:
            raise ExecutorIntegrityError("request head maps to multiple attempts")
        return found[0] if found else None

    def _validated_opened_marker(
        self,
        marker: Mapping[str, Any],
        *,
        request_head: str | None = None,
    ) -> tuple[witness.CallDescriptor, dict[str, Any]]:
        if not isinstance(marker, Mapping) or set(marker) != {
            "kind",
            "schema_version",
            "run_id",
            "request_head",
            "descriptor",
            "opened_receipt",
        }:
            raise ExecutorIntegrityError("opened executor marker fields changed")
        if (
            marker.get("kind") != "experiment_executor_opened"
            or marker.get("schema_version") != EXECUTOR_SCHEMA_VERSION
            or marker.get("run_id") != self.ledger.run_id
            or (
                request_head is not None
                and marker.get("request_head") != request_head
            )
        ):
            raise ExecutorIntegrityError("opened executor marker identity changed")
        try:
            descriptor = witness.CallDescriptor(**marker["descriptor"])
        except (TypeError, KeyError) as exc:
            raise ExecutorIntegrityError("opened call descriptor changed") from exc
        opened = marker.get("opened_receipt")
        if not isinstance(opened, dict):
            raise ExecutorIntegrityError("opened witness receipt changed")
        try:
            self.ledger.authenticator.require_valid_receipt(opened)
        except (OSError, ValueError) as exc:
            raise ExecutorIntegrityError("opened witness signature changed") from exc
        body = opened.get("body")
        if (
            not isinstance(body, Mapping)
            or body.get("event") != "call_opened"
            or body.get("run_id") != self.ledger.run_id
            or body.get("prev_receipt_sha256") != marker.get("request_head")
            or body.get("phase") != descriptor.phase
            or body.get("schedule_index") != descriptor.schedule_index
            or body.get("attempt_number") != descriptor.attempt_number
            or body.get("call_commitment") != descriptor.call_commitment
            or opened not in self.ledger.receipts()
        ):
            raise ExecutorIntegrityError("opened witness receipt binding changed")
        return descriptor, opened

    def _freeze_capture(
        self,
        raw_dir: Path,
        termination: DriverTermination,
        *,
        pre_spawn_runtime_sha256: str,
    ) -> dict[str, Any]:
        files: dict[str, str] = {}
        errors: list[str] = []
        if not raw_dir.is_dir() or raw_dir.is_symlink():
            errors.append("raw_capture_directory_invalid")
        else:
            children = sorted(raw_dir.iterdir(), key=lambda item: item.name)
            for path in children:
                if path.name not in CAPTURE_FILE_NAMES:
                    errors.append(f"unexpected_capture_file:{path.name}")
                    continue
                try:
                    raw = self._read_raw_capture_file(path)
                except ExecutorIntegrityError:
                    errors.append(f"invalid_capture_file:{path.name}")
                    continue
                files[path.name] = base64.b64encode(raw).decode("ascii")
        return {
            "kind": "experiment_executor_capture",
            "schema_version": EXECUTOR_SCHEMA_VERSION,
            "termination": asdict(termination),
            "pre_spawn_runtime_sha256": pre_spawn_runtime_sha256,
            "files_base64": files,
            "integrity_errors": errors,
        }

    @staticmethod
    def _read_raw_capture_file(path: Path) -> bytes:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
            try:
                status = os.fstat(descriptor)
                path_status = path.lstat()
                if (
                    not stat.S_ISREG(status.st_mode)
                    or status.st_uid != os.geteuid()
                    or (status.st_dev, status.st_ino)
                    != (path_status.st_dev, path_status.st_ino)
                ):
                    raise ExecutorIntegrityError("raw capture file changed")
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise ExecutorIntegrityError("raw capture file is unavailable") from exc
        return b"".join(chunks)

    def _load_or_build_bundle(
        self,
        attempt_dir: Path,
        *,
        request_head: str,
        descriptor: witness.CallDescriptor,
        invocation: SealedInvocation,
    ) -> tuple[dict[str, Any], bytes, str]:
        bundle_path = attempt_dir / "bundle.json"
        if bundle_path.exists():
            bundle = self._read_canonical(bundle_path)
            raw = witness.canonical_json_bytes(bundle) + b"\n"
        else:
            capture = self._read_canonical(attempt_dir / "capture.json")
            derived = self.decoder.decode(
                capture,
                expected_runtime_sha256=invocation.runtime_sha256,
                output_schema=invocation.output_schema,
            )
            bundle = {
                "kind": "experiment_executor_artifact_bundle",
                "schema_version": EXECUTOR_SCHEMA_VERSION,
                "run_id": self.ledger.run_id,
                "request_head": request_head,
                "descriptor": asdict(descriptor),
                "artifact_ref": uuid.uuid4().hex,
                "capture": capture,
                "derived": {
                    "outcome": derived.outcome,
                    "token_usage": derived.token_usage,
                    "reason": derived.reason,
                },
            }
            raw = witness.canonical_json_bytes(bundle) + b"\n"
            self._publish_bytes(bundle_path, raw)
        if raw != witness.canonical_json_bytes(bundle) + b"\n":
            raise ExecutorIntegrityError("artifact bundle is not canonical")
        if (
            set(bundle) != {
                "kind",
                "schema_version",
                "run_id",
                "request_head",
                "descriptor",
                "artifact_ref",
                "capture",
                "derived",
            }
            or
            bundle.get("kind") != "experiment_executor_artifact_bundle"
            or bundle.get("schema_version") != EXECUTOR_SCHEMA_VERSION
            or bundle.get("run_id") != self.ledger.run_id
            or bundle.get("request_head") != request_head
            or bundle.get("descriptor") != asdict(descriptor)
            or not isinstance(bundle.get("artifact_ref"), str)
            or re.fullmatch(r"[0-9a-f]{32}", bundle["artifact_ref"]) is None
        ):
            raise ExecutorIntegrityError("artifact bundle identity changed")
        expected_derived = self.decoder.decode(
            bundle["capture"],
            expected_runtime_sha256=invocation.runtime_sha256,
            output_schema=invocation.output_schema,
        )
        if bundle.get("derived") != {
            "outcome": expected_derived.outcome,
            "token_usage": expected_derived.token_usage,
            "reason": expected_derived.reason,
        }:
            raise ExecutorIntegrityError("artifact derived receipt changed")
        commitment = witness.keyed_commitment(
            self.commitment_key,
            domain="executor-artifact",
            payload=raw,
        )
        return bundle, raw, commitment

    def _result_from_marker(self, marker: Mapping[str, Any]) -> ExecutionBundle:
        if not isinstance(marker, Mapping) or set(marker) != {
            "kind",
            "schema_version",
            "run_id",
            "request_head",
            "witness_head",
            "outcome",
            "token_usage",
            "artifact_ref",
            "artifact_root_commitment",
            "opened_receipt",
            "closed_receipt",
            "reason",
        }:
            raise ExecutorIntegrityError("executor result fields changed")
        if (
            marker.get("kind") != "experiment_executor_result"
            or marker.get("schema_version") != EXECUTOR_SCHEMA_VERSION
        ):
            raise ExecutorIntegrityError("executor result identity changed")
        try:
            result = ExecutionBundle(
                run_id=marker["run_id"],
                request_head=marker["request_head"],
                witness_head=marker["witness_head"],
                outcome=marker["outcome"],
                token_usage=copy.deepcopy(marker["token_usage"]),
                artifact_ref=marker["artifact_ref"],
                artifact_root_commitment=marker["artifact_root_commitment"],
                opened_receipt=copy.deepcopy(marker["opened_receipt"]),
                closed_receipt=copy.deepcopy(marker["closed_receipt"]),
                reason=marker["reason"],
            )
        except (KeyError, TypeError) as exc:
            raise ExecutorIntegrityError("executor result fields changed") from exc
        if result.run_id != self.ledger.run_id:
            raise ExecutorIntegrityError("executor result run changed")
        _require_hex64(result.request_head, "result request head")
        _require_hex64(result.witness_head, "result witness head")
        _require_hex64(
            result.artifact_root_commitment, "result artifact commitment"
        )
        if (
            result.outcome not in witness.OUTCOMES
            or not isinstance(result.reason, str)
            or not result.reason
            or not isinstance(result.artifact_ref, str)
            or re.fullmatch(r"[0-9a-f]{32}", result.artifact_ref) is None
        ):
            raise ExecutorIntegrityError("executor result semantics changed")
        try:
            self.ledger.authenticator.require_valid_receipt(result.opened_receipt)
            self.ledger.authenticator.require_valid_receipt(result.closed_receipt)
        except (OSError, ValueError) as exc:
            raise ExecutorIntegrityError("executor result signature changed") from exc
        opened_digest = witness.receipt_sha256(result.opened_receipt)
        closed_body = result.closed_receipt.get("body")
        if (
            result.opened_receipt not in self.ledger.receipts()
            or result.closed_receipt not in self.ledger.receipts()
            or not isinstance(closed_body, Mapping)
            or closed_body.get("event") != "call_closed"
            or closed_body.get("run_id") != result.run_id
            or closed_body.get("prev_receipt_sha256") != opened_digest
            or closed_body.get("opened_receipt_sha256") != opened_digest
            or closed_body.get("outcome") != result.outcome
            or closed_body.get("token_usage") != result.token_usage
            or closed_body.get("artifact_root_commitment")
            != result.artifact_root_commitment
            or witness.receipt_sha256(result.closed_receipt) != result.witness_head
            or result.opened_receipt.get("body", {}).get("prev_receipt_sha256")
            != result.request_head
        ):
            raise ExecutorIntegrityError("executor result witness binding changed")
        return result

    def _validated_artifact_bytes(
        self, attempt_dir: Path, result: ExecutionBundle
    ) -> bytes:
        bundle = self._read_canonical(attempt_dir / "bundle.json")
        raw = witness.canonical_json_bytes(bundle) + b"\n"
        commitment = witness.keyed_commitment(
            self.commitment_key,
            domain="executor-artifact",
            payload=raw,
        )
        if (
            bundle.get("artifact_ref") != result.artifact_ref
            or commitment != result.artifact_root_commitment
            or result.closed_receipt.get("body", {}).get(
                "artifact_root_commitment"
            )
            != commitment
        ):
            raise ExecutorIntegrityError("artifact commitment changed")
        return raw

    def _finalize_captured(
        self,
        attempt_dir: Path,
        *,
        request_head: str,
        descriptor: witness.CallDescriptor,
        invocation: SealedInvocation,
        opened_receipt: dict[str, Any],
    ) -> ExecutionBundle:
        bundle, _raw, artifact_commitment = self._load_or_build_bundle(
            attempt_dir,
            request_head=request_head,
            descriptor=descriptor,
            invocation=invocation,
        )
        derived = bundle.get("derived")
        if not isinstance(derived, Mapping):
            raise ExecutorIntegrityError("artifact derived receipt changed")
        opened_digest = witness.receipt_sha256(opened_receipt)
        close_request = {
            "kind": "experiment_executor_close_request",
            "schema_version": EXECUTOR_SCHEMA_VERSION,
            "run_id": self.ledger.run_id,
            "request_head": request_head,
            "opened_receipt_sha256": opened_digest,
            "outcome": derived.get("outcome"),
            "artifact_ref": bundle["artifact_ref"],
            "artifact_root_commitment": artifact_commitment,
            "token_usage": copy.deepcopy(derived.get("token_usage")),
            "reason": derived.get("reason"),
        }
        close_path = attempt_dir / "close_request.json"
        if close_path.exists():
            persisted = self._read_canonical(close_path)
            if persisted != close_request:
                raise ExecutorIntegrityError("executor close request changed")
        else:
            self._publish_marker(close_path, close_request)
        closed = self.ledger.close_call(
            opened_receipt_sha256=opened_digest,
            outcome=close_request["outcome"],
            artifact_root_commitment=artifact_commitment,
            token_usage=close_request["token_usage"],
            expected_head=opened_digest,
        )
        self.checkpoint("after_close")
        marker = {
            "kind": "experiment_executor_result",
            "schema_version": EXECUTOR_SCHEMA_VERSION,
            "run_id": self.ledger.run_id,
            "request_head": request_head,
            "witness_head": witness.receipt_sha256(closed),
            "outcome": close_request["outcome"],
            "token_usage": close_request["token_usage"],
            "artifact_ref": close_request["artifact_ref"],
            "artifact_root_commitment": artifact_commitment,
            "opened_receipt": opened_receipt,
            "closed_receipt": closed,
            "reason": close_request["reason"],
        }
        result_path = attempt_dir / "result.json"
        if result_path.exists():
            persisted_result = self._read_canonical(result_path)
            if persisted_result != marker:
                raise ExecutorIntegrityError("executor result changed")
        else:
            self._publish_marker(result_path, marker)
        self.checkpoint("after_result")
        return self._result_from_marker(marker)

    def _terminalize_indeterminate(
        self,
        attempt_dir: Path,
        *,
        request_head: str,
        descriptor: witness.CallDescriptor,
        opened_receipt: dict[str, Any],
    ) -> ExecutionBundle:
        indeterminate = self._read_canonical(attempt_dir / "indeterminate.json")
        if (
            indeterminate.get("kind") != "experiment_executor_indeterminate"
            or indeterminate.get("schema_version") != EXECUTOR_SCHEMA_VERSION
            or indeterminate.get("run_id") != self.ledger.run_id
            or indeterminate.get("request_head") != request_head
            or indeterminate.get("opened_receipt_sha256")
            != witness.receipt_sha256(opened_receipt)
            or not isinstance(indeterminate.get("reason"), str)
            or not indeterminate["reason"]
        ):
            raise ExecutorIntegrityError("indeterminate executor marker changed")
        usage = _unknown_usage("unavailable")
        bundle_path = attempt_dir / "bundle.json"
        if bundle_path.exists():
            bundle = self._read_canonical(bundle_path)
            raw = witness.canonical_json_bytes(bundle) + b"\n"
        else:
            bundle = {
                "kind": "experiment_executor_indeterminate_bundle",
                "schema_version": EXECUTOR_SCHEMA_VERSION,
                "run_id": self.ledger.run_id,
                "request_head": request_head,
                "descriptor": asdict(descriptor),
                "artifact_ref": uuid.uuid4().hex,
                "indeterminate": indeterminate,
                "derived": {
                    "outcome": "indeterminate",
                    "token_usage": usage,
                    "reason": indeterminate["reason"],
                },
            }
            raw = witness.canonical_json_bytes(bundle) + b"\n"
            self._publish_bytes(bundle_path, raw)
        if (
            set(bundle) != {
                "kind",
                "schema_version",
                "run_id",
                "request_head",
                "descriptor",
                "artifact_ref",
                "indeterminate",
                "derived",
            }
            or bundle.get("kind") != "experiment_executor_indeterminate_bundle"
            or bundle.get("schema_version") != EXECUTOR_SCHEMA_VERSION
            or bundle.get("run_id") != self.ledger.run_id
            or bundle.get("request_head") != request_head
            or bundle.get("descriptor") != asdict(descriptor)
            or bundle.get("indeterminate") != indeterminate
            or bundle.get("derived")
            != {
                "outcome": "indeterminate",
                "token_usage": usage,
                "reason": indeterminate["reason"],
            }
            or not isinstance(bundle.get("artifact_ref"), str)
            or re.fullmatch(r"[0-9a-f]{32}", bundle["artifact_ref"]) is None
        ):
            raise ExecutorIntegrityError("indeterminate artifact bundle changed")
        artifact_commitment = witness.keyed_commitment(
            self.commitment_key,
            domain="executor-artifact",
            payload=raw,
        )
        opened_digest = witness.receipt_sha256(opened_receipt)
        close_request = {
            "kind": "experiment_executor_close_request",
            "schema_version": EXECUTOR_SCHEMA_VERSION,
            "run_id": self.ledger.run_id,
            "request_head": request_head,
            "opened_receipt_sha256": opened_digest,
            "outcome": "indeterminate",
            "artifact_ref": bundle["artifact_ref"],
            "artifact_root_commitment": artifact_commitment,
            "token_usage": usage,
            "reason": indeterminate["reason"],
        }
        close_path = attempt_dir / "close_request.json"
        if close_path.exists():
            if self._read_canonical(close_path) != close_request:
                raise ExecutorIntegrityError("indeterminate close request changed")
        else:
            self._publish_marker(close_path, close_request)
        closed = self.ledger.close_call(
            opened_receipt_sha256=opened_digest,
            outcome="indeterminate",
            artifact_root_commitment=artifact_commitment,
            token_usage=usage,
            expected_head=opened_digest,
        )
        marker = {
            "kind": "experiment_executor_result",
            "schema_version": EXECUTOR_SCHEMA_VERSION,
            "run_id": self.ledger.run_id,
            "request_head": request_head,
            "witness_head": witness.receipt_sha256(closed),
            "outcome": "indeterminate",
            "token_usage": usage,
            "artifact_ref": bundle["artifact_ref"],
            "artifact_root_commitment": artifact_commitment,
            "opened_receipt": opened_receipt,
            "closed_receipt": closed,
            "reason": indeterminate["reason"],
        }
        result_path = attempt_dir / "result.json"
        if result_path.exists():
            if self._read_canonical(result_path) != marker:
                raise ExecutorIntegrityError("indeterminate result changed")
        else:
            self._publish_marker(result_path, marker)
        return self._result_from_marker(marker)

    def _resume_attempt(
        self,
        attempt_dir: Path,
        *,
        request_head: str,
        descriptor: witness.CallDescriptor,
        invocation: SealedInvocation,
        opened_receipt: dict[str, Any],
    ) -> ExecutionBundle:
        result_path = attempt_dir / "result.json"
        if result_path.exists():
            result = self._result_from_marker(self._read_canonical(result_path))
            self._validated_artifact_bytes(attempt_dir, result)
            if result.outcome == "indeterminate":
                raise ExecutorIndeterminateError(
                    "witnessed attempt is terminally indeterminate",
                    result=result,
                )
            return result
        if (attempt_dir / "indeterminate.json").exists():
            result = self._terminalize_indeterminate(
                attempt_dir,
                request_head=request_head,
                descriptor=descriptor,
                opened_receipt=opened_receipt,
            )
            raise ExecutorIndeterminateError(
                "witnessed attempt is terminally indeterminate",
                result=result,
            )
        if (attempt_dir / "close_request.json").exists() or (
            attempt_dir / "capture.json"
        ).exists():
            return self._finalize_captured(
                attempt_dir,
                request_head=request_head,
                descriptor=descriptor,
                invocation=invocation,
                opened_receipt=opened_receipt,
            )
        spawn_path = attempt_dir / "spawn_intent.json"
        if spawn_path.exists():
            spawn = self._read_canonical(spawn_path)
            expected_spawn = self._expected_spawn_marker(
                request_head=request_head,
                opened_receipt=opened_receipt,
                invocation=invocation,
            )
            if spawn != expected_spawn:
                raise ExecutorIntegrityError("spawn intent binding changed")
            marker = {
                "kind": "experiment_executor_indeterminate",
                "schema_version": EXECUTOR_SCHEMA_VERSION,
                "run_id": self.ledger.run_id,
                "request_head": request_head,
                "opened_receipt_sha256": witness.receipt_sha256(opened_receipt),
                "reason": "spawn_intent_without_durable_capture",
            }
            indeterminate_path = attempt_dir / "indeterminate.json"
            if not indeterminate_path.exists():
                self._publish_marker(indeterminate_path, marker)
            result = self._terminalize_indeterminate(
                attempt_dir,
                request_head=request_head,
                descriptor=descriptor,
                opened_receipt=opened_receipt,
            )
            raise ExecutorIndeterminateError(
                "witnessed attempt is terminally indeterminate",
                result=result,
            )

        pre_spawn_runtime_sha256 = self._runtime_sha256(invocation.runtime_path)
        if pre_spawn_runtime_sha256 != invocation.runtime_sha256:
            marker = {
                "kind": "experiment_executor_indeterminate",
                "schema_version": EXECUTOR_SCHEMA_VERSION,
                "run_id": self.ledger.run_id,
                "request_head": request_head,
                "opened_receipt_sha256": witness.receipt_sha256(opened_receipt),
                "reason": "sealed_runtime_changed_before_spawn",
            }
            self._publish_marker(attempt_dir / "indeterminate.json", marker)
            result = self._terminalize_indeterminate(
                attempt_dir,
                request_head=request_head,
                descriptor=descriptor,
                opened_receipt=opened_receipt,
            )
            raise ExecutorIndeterminateError(
                "sealed runtime changed before spawn; run is terminally blocked",
                result=result,
            )
        spawn_marker = self._expected_spawn_marker(
            request_head=request_head,
            opened_receipt=opened_receipt,
            invocation=invocation,
        )
        self._publish_marker(spawn_path, spawn_marker)
        self.checkpoint("after_spawn_intent")
        raw_dir = attempt_dir / "raw"
        raw_dir.mkdir(exist_ok=False, mode=0o700)
        self._require_private_directory(raw_dir, "executor raw capture")
        try:
            driver_termination = self.driver.invoke(invocation, raw_dir)
            if not isinstance(driver_termination, DriverTermination):
                raise ExecutorProtocolError("model driver returned invalid termination")
            post_runtime_sha256 = self._runtime_sha256(invocation.runtime_path)
            termination = DriverTermination(
                exit_code=driver_termination.exit_code,
                timed_out=driver_termination.timed_out,
                runtime_sha256=post_runtime_sha256,
            )
        except Exception as exc:
            marker = {
                "kind": "experiment_executor_indeterminate",
                "schema_version": EXECUTOR_SCHEMA_VERSION,
                "run_id": self.ledger.run_id,
                "request_head": request_head,
                "opened_receipt_sha256": witness.receipt_sha256(opened_receipt),
                "reason": f"driver_failed_after_spawn:{type(exc).__name__}",
            }
            if not (attempt_dir / "indeterminate.json").exists():
                self._publish_marker(attempt_dir / "indeterminate.json", marker)
            result = self._terminalize_indeterminate(
                attempt_dir,
                request_head=request_head,
                descriptor=descriptor,
                opened_receipt=opened_receipt,
            )
            raise ExecutorIndeterminateError(
                "driver failed after durable spawn intent; run is terminally blocked",
                result=result,
            ) from exc
        capture = self._freeze_capture(
            raw_dir,
            termination,
            pre_spawn_runtime_sha256=pre_spawn_runtime_sha256,
        )
        self._publish_marker(attempt_dir / "capture.json", capture)
        self.checkpoint("after_capture")
        return self._finalize_captured(
            attempt_dir,
            request_head=request_head,
            descriptor=descriptor,
            invocation=invocation,
            opened_receipt=opened_receipt,
        )

    def execute_next(self, *, run_id: str, expected_head: str) -> ExecutionBundle:
        """Execute or recover the exact next call in the witnessed schedule."""

        if run_id != self.ledger.run_id:
            raise ExecutorProtocolError("executor run ID changed")
        _require_hex64(expected_head, "expected witness head")
        with self._locked():
            existing = self._find_attempt_for_request(expected_head)
            if existing is not None:
                opened_marker = self._read_canonical(existing / "opened.json")
                descriptor, opened_receipt = self._validated_opened_marker(
                    opened_marker, request_head=expected_head
                )
                invocation = self._invocation_for_descriptor(descriptor)
                return self._resume_attempt(
                    existing,
                    request_head=expected_head,
                    descriptor=descriptor,
                    invocation=invocation,
                    opened_receipt=opened_receipt,
                )

            status = self.ledger.status()
            if status["state"] not in {"active", "open"}:
                raise ExecutorProtocolError(
                    f"witness run cannot execute from state {status['state']}"
                )
            descriptor, invocation = self._descriptor_for_status(status)
            if status["state"] == "active" and status["head"] != expected_head:
                raise ExecutorProtocolError("expected witness head is stale or changed")
            try:
                opened = self.ledger.open_call(
                    descriptor, expected_head=expected_head
                )
            except witness.WitnessProtocolError as exc:
                raise ExecutorProtocolError(str(exc)) from exc
            attempt_dir = self._attempt_dir(descriptor)
            opened_marker = self._opened_marker(
                request_head=expected_head,
                descriptor=descriptor,
                opened_receipt=opened,
            )
            opened_path = attempt_dir / "opened.json"
            if opened_path.exists():
                if self._read_canonical(opened_path) != opened_marker:
                    raise ExecutorIntegrityError("opened executor marker changed")
            else:
                self._publish_marker(opened_path, opened_marker)
            self.checkpoint("after_open")
            return self._resume_attempt(
                attempt_dir,
                request_head=expected_head,
                descriptor=descriptor,
                invocation=invocation,
                opened_receipt=opened,
            )

    def status(self, *, run_id: str) -> dict[str, Any]:
        if run_id != self.ledger.run_id:
            raise ExecutorProtocolError("executor run ID changed")
        with self._locked():
            attempts: list[dict[str, Any]] = []
            for path in sorted(self.attempts_dir.iterdir(), key=lambda item: item.name):
                if path.is_symlink() or not path.is_dir():
                    raise ExecutorIntegrityError("executor attempt inventory changed")
                self._require_private_directory(path, "executor attempt")
                opened = self._read_canonical(path / "opened.json")
                descriptor, opened_receipt = self._validated_opened_marker(opened)
                invocation = self._invocation_for_descriptor(descriptor)
                state = "opened"
                for marker, candidate in (
                    ("spawn_intent.json", "spawn_intent"),
                    ("capture.json", "captured"),
                    ("close_request.json", "closing"),
                    ("result.json", "closed"),
                    ("indeterminate.json", "indeterminate"),
                ):
                    if (path / marker).exists():
                        marker_value = self._read_canonical(path / marker)
                        if marker == "spawn_intent.json" and marker_value != (
                            self._expected_spawn_marker(
                                request_head=opened["request_head"],
                                opened_receipt=opened_receipt,
                                invocation=invocation,
                            )
                        ):
                            raise ExecutorIntegrityError("spawn intent binding changed")
                        if marker == "result.json":
                            result = self._result_from_marker(marker_value)
                            self._validated_artifact_bytes(path, result)
                        state = candidate
                attempts.append(
                    {
                        "attempt_id": path.name,
                        "descriptor": opened["descriptor"],
                        "request_head": opened["request_head"],
                        "state": state,
                    }
                )
            return {
                "run_id": self.ledger.run_id,
                "witness": self.ledger.status(),
                "signed_receipts": list(self.ledger.receipts()),
                "attempts": attempts,
            }

    def export_completed_run(self) -> dict[str, Any]:
        """Return one fully revalidated, gold-free export after run completion.

        This method is intentionally not exposed by the restricted SSH service.
        It is for an executor-owned postprocessor that runs under the same trust
        principal after the sealed witness schedule is complete.
        """

        with self._locked():
            status = self.ledger.status()
            if (
                status.get("state") != "complete"
                or status.get("schedule_position") != len(self.invocations)
                or status.get("model_calls_reserved")
                != status.get("model_calls_closed")
            ):
                raise ExecutorProtocolError(
                    "sealed run must be complete before artifact export"
                )
            receipts = list(self.ledger.receipts())
            attempts: list[dict[str, Any]] = []
            for path in sorted(self.attempts_dir.iterdir(), key=lambda item: item.name):
                if path.is_symlink() or not path.is_dir():
                    raise ExecutorIntegrityError(
                        "executor attempt inventory changed before export"
                    )
                self._require_private_directory(path, "executor attempt")
                opened = self._read_canonical(path / "opened.json")
                descriptor, _opened_receipt = self._validated_opened_marker(opened)
                invocation = self._invocation_for_descriptor(descriptor)
                result_path = path / "result.json"
                if not result_path.exists():
                    raise ExecutorIntegrityError(
                        "completed executor attempt has no result"
                    )
                result = self._result_from_marker(self._read_canonical(result_path))
                raw = self._validated_artifact_bytes(path, result)
                attempts.append(
                    {
                        "descriptor": asdict(descriptor),
                        "request_head": result.request_head,
                        "witness_head": result.witness_head,
                        "outcome": result.outcome,
                        "token_usage": copy.deepcopy(result.token_usage),
                        "artifact_root_commitment": result.artifact_root_commitment,
                        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                        "artifact_bytes": len(raw),
                        "artifact_base64": base64.b64encode(raw).decode("ascii"),
                        "opened_receipt": copy.deepcopy(result.opened_receipt),
                        "closed_receipt": copy.deepcopy(result.closed_receipt),
                    }
                )

            if len(attempts) != status["model_calls_closed"]:
                raise ExecutorIntegrityError(
                    "executor attempt inventory differs from witnessed calls"
                )
            attempts.sort(
                key=lambda row: (
                    row["descriptor"]["schedule_index"],
                    row["descriptor"]["attempt_number"],
                )
            )
            accepted_slots = 0
            for item, invocation in zip(
                self.ledger.schedule,
                self.invocations,
                strict=True,
            ):
                slot = [
                    row
                    for row in attempts
                    if row["descriptor"]["phase"] == item.phase
                    and row["descriptor"]["schedule_index"]
                    == item.schedule_index
                ]
                if (
                    not slot
                    or [row["descriptor"]["attempt_number"] for row in slot]
                    != list(range(1, len(slot) + 1))
                    or any(
                        row["descriptor"]["call_commitment"]
                        != invocation.call_commitment(self.commitment_key)
                        for row in slot
                    )
                    or any(row["outcome"] != "provider_failure" for row in slot[:-1])
                    or slot[-1]["outcome"] != "accepted"
                ):
                    raise ExecutorIntegrityError(
                        "completed executor slot has invalid attempt history"
                    )
                accepted_slots += 1
            if accepted_slots != len(self.invocations):
                raise ExecutorIntegrityError(
                    "completed executor accepted coverage changed"
                )
            return {
                "schema_version": "experiment-run-export-v1",
                "run_id": self.ledger.run_id,
                "witness_head": status["head"],
                "schedule_length": len(self.invocations),
                "accepted_slots": accepted_slots,
                "model_calls_reserved": status["model_calls_reserved"],
                "model_calls_closed": status["model_calls_closed"],
                "signed_receipts": receipts,
                "attempts": attempts,
            }

    def fetch_artifact(self, *, run_id: str, artifact_ref: str) -> bytes:
        if run_id != self.ledger.run_id:
            raise ExecutorProtocolError("executor run ID changed")
        if not isinstance(artifact_ref, str) or re.fullmatch(
            r"[0-9a-f]{32}", artifact_ref
        ) is None:
            raise ExecutorProtocolError("artifact reference is invalid")
        with self._locked():
            matches: list[tuple[Path, dict[str, Any]]] = []
            for path in sorted(self.attempts_dir.iterdir(), key=lambda item: item.name):
                result_path = path / "result.json"
                if result_path.exists():
                    result = self._read_canonical(result_path)
                    parsed = self._result_from_marker(result)
                    if parsed.artifact_ref == artifact_ref:
                        matches.append((path, result))
            if len(matches) != 1:
                raise ExecutorIntegrityError("artifact reference is absent or ambiguous")
            attempt_dir, result = matches[0]
            parsed = self._result_from_marker(result)
            return self._validated_artifact_bytes(attempt_dir, parsed)
