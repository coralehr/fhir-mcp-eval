#!/usr/bin/env python3
"""PHI-free, signed monotonic witness for experiment model-call inventories.

This module is designed to run under a principal that the experiment runner
cannot mutate.  It does not make a same-user filesystem trustworthy.  The
runner sees signed commitments and counters; prompts, answers, question IDs,
paths, and raw artifact hashes never cross the seam.
"""

from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import hmac
import json
import os
import re
import stat
import struct
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence


GENESIS_HEAD = "0" * 64
SCHEMA_VERSION = "experiment-witness-v2"
RUN_SCHEMA_VERSION = "experiment-witness-run-v2"
SIGNATURE_NAMESPACE = "coralehr-experiment-witness-v2"
SIGNATURE_DOMAIN = b"coralehr-experiment-witness-v2\x00"
PHASES = ("answer", "panel")
OUTCOMES = ("accepted", "provider_failure", "contaminated", "indeterminate")
TOKEN_VALUE_KEYS = ("input", "cached", "output", "reasoning", "total")
TOKEN_KEYS = (*TOKEN_VALUE_KEYS, "complete", "source")
TOKEN_USAGE_SOURCES = (
    "turn.completed",
    "provider.error",
    "partial.capture",
    "unavailable",
)
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_EVENT_NAME = re.compile(r"^[0-9]{20}\.json$")
SSH_KEYGEN_PATH = Path("/usr/bin/ssh-keygen")
SSH_KEYGEN_TIMEOUT_SECONDS = 10
SSH_KEYGEN_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}


class WitnessProtocolError(ValueError):
    """The caller requested a transition forbidden by the sealed schedule."""


class WitnessIntegrityError(ValueError):
    """The persisted signed chain or witness identity failed verification."""


@dataclass(frozen=True)
class ScheduleItem:
    phase: Literal["answer", "panel"]
    schedule_index: int
    call_commitment: str
    max_attempts: int


@dataclass(frozen=True)
class CallDescriptor:
    phase: Literal["answer", "panel"]
    schedule_index: int
    attempt_number: int
    call_commitment: str


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def keyed_commitment(secret_key: bytes, *, domain: str, payload: bytes) -> str:
    """Return a domain-separated HMAC commitment safe for public receipts."""

    if not isinstance(secret_key, bytes) or len(secret_key) < 32:
        raise WitnessProtocolError("commitment key must contain at least 32 bytes")
    if (
        not isinstance(domain, str)
        or re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", domain) is None
    ):
        raise WitnessProtocolError("commitment domain is invalid")
    if not isinstance(payload, bytes):
        raise WitnessProtocolError("commitment payload must be immutable bytes")
    message = b"coralehr-experiment-commitment-v1\x00" + domain.encode("ascii")
    message += b"\x00" + len(payload).to_bytes(8, "big") + payload
    return hmac.new(secret_key, message, hashlib.sha256).hexdigest()


def receipt_sha256(receipt: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(receipt))


def _require_hex64(value: Any, label: str) -> str:
    if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
        raise WitnessProtocolError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _call_id(
    run_id: str,
    *,
    phase: str,
    schedule_index: int,
    attempt_number: int,
) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "run_id": run_id,
                "phase": phase,
                "schedule_index": schedule_index,
                "attempt_number": attempt_number,
            }
        )
    )


def _validate_schedule(schedule: Sequence[ScheduleItem]) -> tuple[ScheduleItem, ...]:
    result = tuple(schedule)
    if not result:
        raise WitnessProtocolError("witness schedule must not be empty")
    seen: set[tuple[str, int]] = set()
    phase_position = 0
    last_index: dict[str, int] = {}
    for item in result:
        if not isinstance(item, ScheduleItem):
            raise WitnessProtocolError("witness schedule contains an invalid item")
        if item.phase not in PHASES:
            raise WitnessProtocolError("witness schedule phase is invalid")
        new_phase_position = PHASES.index(item.phase)
        if new_phase_position < phase_position:
            raise WitnessProtocolError("witness schedule phases are not monotonic")
        phase_position = new_phase_position
        if type(item.schedule_index) is not int or item.schedule_index < 0:
            raise WitnessProtocolError("witness schedule index is invalid")
        expected_index = last_index.get(item.phase, -1) + 1
        if item.schedule_index != expected_index:
            raise WitnessProtocolError("witness schedule indexes are not contiguous")
        last_index[item.phase] = item.schedule_index
        key = (item.phase, item.schedule_index)
        if key in seen:
            raise WitnessProtocolError("witness schedule contains a duplicate item")
        seen.add(key)
        _require_hex64(item.call_commitment, "call commitment")
        if type(item.max_attempts) is not int or not 1 <= item.max_attempts <= 100:
            raise WitnessProtocolError("witness max attempts is invalid")
    return result


def _validate_call_descriptor(descriptor: CallDescriptor) -> None:
    if not isinstance(descriptor, CallDescriptor):
        raise WitnessProtocolError("call descriptor is invalid")
    if descriptor.phase not in PHASES:
        raise WitnessProtocolError("call phase is invalid")
    if type(descriptor.schedule_index) is not int or descriptor.schedule_index < 0:
        raise WitnessProtocolError("call schedule index is invalid")
    if type(descriptor.attempt_number) is not int or descriptor.attempt_number < 1:
        raise WitnessProtocolError("call attempt number is invalid")
    _require_hex64(descriptor.call_commitment, "call commitment")


def _validate_token_usage(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(TOKEN_KEYS):
        raise WitnessProtocolError("token usage fields are invalid")
    complete = value["complete"]
    source = value["source"]
    if type(complete) is not bool:
        raise WitnessProtocolError("token usage completeness is invalid")
    if source not in TOKEN_USAGE_SOURCES:
        raise WitnessProtocolError("token usage source is invalid")
    usage: dict[str, Any] = {}
    for key in TOKEN_VALUE_KEYS:
        amount = value[key]
        if amount is not None and (type(amount) is not int or amount < 0):
            raise WitnessProtocolError(
                "token usage values must be nonnegative integers or null"
            )
        usage[key] = amount
    usage["complete"] = complete
    usage["source"] = source
    if complete and any(usage[key] is None for key in TOKEN_VALUE_KEYS):
        raise WitnessProtocolError("complete token usage has missing values")
    if not complete and all(usage[key] is not None for key in TOKEN_VALUE_KEYS):
        raise WitnessProtocolError(
            "incomplete token usage must contain an unknown value"
        )
    if complete and source not in {"turn.completed", "provider.error"}:
        raise WitnessProtocolError("complete token usage source is invalid")
    if not complete and source == "turn.completed":
        raise WitnessProtocolError("incomplete token usage source is invalid")
    if source == "unavailable" and any(
        usage[key] is not None for key in TOKEN_VALUE_KEYS
    ):
        raise WitnessProtocolError("unavailable token usage must contain null values")
    if (
        usage["cached"] is not None
        and usage["input"] is not None
        and usage["cached"] > usage["input"]
    ):
        raise WitnessProtocolError("cached token usage exceeds input token usage")
    if (
        usage["reasoning"] is not None
        and usage["output"] is not None
        and usage["reasoning"] > usage["output"]
    ):
        raise WitnessProtocolError("reasoning token usage exceeds output token usage")
    if (
        usage["total"] is not None
        and usage["input"] is not None
        and usage["output"] is not None
        and usage["total"] != usage["input"] + usage["output"]
    ):
        raise WitnessProtocolError("total token usage does not reconcile")
    return usage


def _validate_outcome_token_usage(outcome: str, usage: Mapping[str, Any]) -> None:
    if outcome == "accepted":
        if usage["complete"] is not True:
            raise WitnessProtocolError("accepted token usage must be complete")
        if usage["source"] != "turn.completed":
            raise WitnessProtocolError(
                "accepted token usage must come from turn.completed"
            )
    elif outcome == "provider_failure" and usage["source"] != "provider.error":
        raise WitnessProtocolError(
            "provider failure token usage must come from provider.error"
        )
    elif outcome == "indeterminate" and usage["source"] == "turn.completed":
        raise WitnessProtocolError(
            "indeterminate token usage cannot come from turn.completed"
        )


class SshEd25519Verifier:
    """Verify witness receipts with only the externally anchored public key."""

    def __init__(self, *, public_key: str, identity: str) -> None:
        if not SSH_KEYGEN_PATH.is_file() or not os.access(SSH_KEYGEN_PATH, os.X_OK):
            raise WitnessIntegrityError("pinned ssh-keygen executable is unavailable")
        if not isinstance(identity, str) or not identity or any(ch.isspace() for ch in identity):
            raise WitnessIntegrityError("witness signer identity is invalid")
        self.identity = identity
        self.public_key = self._normalize_public_key(public_key)
        self.key_id = "sha256:" + sha256_bytes((self.public_key + "\n").encode("ascii"))

    @staticmethod
    def _normalize_public_key(public_key: str) -> str:
        if not isinstance(public_key, str) or "\n" in public_key or "\r" in public_key:
            raise WitnessIntegrityError("witness Ed25519 public key is invalid")
        match = re.fullmatch(r"ssh-ed25519 ([A-Za-z0-9+/]+={0,2})", public_key)
        if match is None:
            raise WitnessIntegrityError("witness Ed25519 public key is invalid")
        try:
            blob = base64.b64decode(match.group(1), validate=True)
        except ValueError as exc:
            raise WitnessIntegrityError("witness Ed25519 public key is invalid") from exc
        expected = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32)
        if len(blob) != len(expected) + 32 or not blob.startswith(expected):
            raise WitnessIntegrityError("witness Ed25519 public key is invalid")
        return public_key

    @staticmethod
    def _signed_payload(body: Mapping[str, Any]) -> bytes:
        return SIGNATURE_DOMAIN + canonical_json_bytes(body)

    def verify_receipt(self, receipt: Mapping[str, Any]) -> bool:
        try:
            self.require_valid_receipt(receipt)
        except (OSError, ValueError, subprocess.SubprocessError):
            return False
        return True

    def require_valid_receipt(self, receipt: Mapping[str, Any]) -> None:
        if not isinstance(receipt, Mapping) or set(receipt) != {
            "kind",
            "schema_version",
            "body",
            "body_sha256",
            "signature",
        }:
            raise WitnessIntegrityError("witness receipt envelope fields changed")
        if (
            receipt.get("kind") != "experiment_witness_receipt"
            or receipt.get("schema_version") != SCHEMA_VERSION
        ):
            raise WitnessIntegrityError("witness receipt envelope identity changed")
        body = receipt.get("body")
        if not isinstance(body, Mapping):
            raise WitnessIntegrityError("witness receipt body is invalid")
        body_bytes = canonical_json_bytes(body)
        if receipt.get("body_sha256") != sha256_bytes(body_bytes):
            raise WitnessIntegrityError("witness receipt body digest changed")
        signature = receipt.get("signature")
        if not isinstance(signature, Mapping) or signature != {
            "algorithm": "ssh-ed25519",
            "identity": self.identity,
            "namespace": SIGNATURE_NAMESPACE,
            "value_base64": signature.get("value_base64"),
        }:
            raise WitnessIntegrityError("witness receipt signature metadata changed")
        try:
            signature_bytes = base64.b64decode(
                signature["value_base64"], validate=True
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WitnessIntegrityError("witness receipt signature encoding changed") from exc
        with tempfile.TemporaryDirectory(prefix="experiment-witness-verify-") as directory:
            root = Path(directory)
            allowed_signers = root / "allowed_signers"
            allowed_signers.write_text(
                f"{self.identity} {self.public_key}\n", encoding="ascii"
            )
            signature_path = root / "receipt.sig"
            signature_path.write_bytes(signature_bytes)
            process = subprocess.run(
                [
                    str(SSH_KEYGEN_PATH),
                    "-Y",
                    "verify",
                    "-q",
                    "-f",
                    str(allowed_signers),
                    "-I",
                    self.identity,
                    "-n",
                    SIGNATURE_NAMESPACE,
                    "-s",
                    str(signature_path),
                ],
                input=self._signed_payload(body),
                check=False,
                capture_output=True,
                env=SSH_KEYGEN_ENV,
                timeout=SSH_KEYGEN_TIMEOUT_SECONDS,
            )
        if process.returncode != 0:
            raise WitnessIntegrityError("witness receipt signature is invalid")


class SshEd25519Authenticator(SshEd25519Verifier):
    """Sign receipts on the witness host and expose public-only verification."""

    def __init__(self, *, private_key_path: Path, identity: str) -> None:
        self.private_key_path = private_key_path.resolve()
        if not self.private_key_path.is_file():
            raise WitnessIntegrityError("witness private key is missing")
        process = subprocess.run(
            [str(SSH_KEYGEN_PATH), "-y", "-f", str(self.private_key_path)],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=SSH_KEYGEN_ENV,
            timeout=SSH_KEYGEN_TIMEOUT_SECONDS,
        )
        if process.returncode != 0 or not process.stdout.strip().startswith("ssh-ed25519 "):
            raise WitnessIntegrityError("witness Ed25519 public key could not be derived")
        derived_fields = process.stdout.strip().split()
        if len(derived_fields) < 2:
            raise WitnessIntegrityError("witness Ed25519 public key could not be derived")
        super().__init__(
            public_key=" ".join(derived_fields[:2]), identity=identity
        )

    def sign_body(self, body: Mapping[str, Any]) -> dict[str, str]:
        payload = self._signed_payload(body)
        with tempfile.TemporaryDirectory(prefix="experiment-witness-sign-") as directory:
            message = Path(directory) / "receipt"
            message.write_bytes(payload)
            process = subprocess.run(
                [
                    str(SSH_KEYGEN_PATH),
                    "-Y",
                    "sign",
                    "-q",
                    "-f",
                    str(self.private_key_path),
                    "-n",
                    SIGNATURE_NAMESPACE,
                    str(message),
                ],
                check=False,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                env=SSH_KEYGEN_ENV,
                timeout=SSH_KEYGEN_TIMEOUT_SECONDS,
            )
            signature_path = message.with_name(message.name + ".sig")
            if process.returncode != 0 or not signature_path.is_file():
                raise WitnessIntegrityError("witness receipt signing failed")
            signature = base64.b64encode(signature_path.read_bytes()).decode("ascii")
        return {
            "algorithm": "ssh-ed25519",
            "identity": self.identity,
            "namespace": SIGNATURE_NAMESPACE,
            "value_base64": signature,
        }


@dataclass
class _ReplayState:
    state: Literal["active", "open", "aborted", "complete"]
    schedule_position: int
    attempt_number: int
    head: str
    receipts: list[dict[str, Any]]
    open_receipt: dict[str, Any] | None
    model_calls_reserved: int
    model_calls_closed: int


class WitnessChainVerifier:
    """Public-key-only verifier for the complete witness state machine."""

    def __init__(
        self,
        *,
        run_id: str,
        schedule: Sequence[ScheduleItem],
        verifier: SshEd25519Verifier,
    ) -> None:
        self.run_id = _require_hex64(run_id, "run id")
        self.schedule = _validate_schedule(schedule)
        self.verifier = verifier

    @staticmethod
    def descriptor_from_body(body: Mapping[str, Any]) -> CallDescriptor:
        try:
            return CallDescriptor(
                phase=body["phase"],
                schedule_index=body["schedule_index"],
                attempt_number=body["attempt_number"],
                call_commitment=body["call_commitment"],
            )
        except (KeyError, TypeError) as exc:
            raise WitnessIntegrityError("witness call descriptor is malformed") from exc

    def _require_body_shape(
        self, body: Mapping[str, Any], *, sequence: int, previous_head: str
    ) -> None:
        if set(body) != {
            "witness_key_id",
            "run_id",
            "seq",
            "prev_receipt_sha256",
            "event",
            "phase",
            "schedule_index",
            "attempt_number",
            "call_id",
            "call_commitment",
            "opened_receipt_sha256",
            "outcome",
            "artifact_root_commitment",
            "token_usage",
            "witnessed_at",
        }:
            raise WitnessIntegrityError("witness receipt body fields changed")
        if (
            body.get("witness_key_id") != self.verifier.key_id
            or body.get("run_id") != self.run_id
            or body.get("seq") != sequence
            or body.get("prev_receipt_sha256") != previous_head
        ):
            raise WitnessIntegrityError("witness receipt chain binding changed")
        descriptor = self.descriptor_from_body(body)
        try:
            _validate_call_descriptor(descriptor)
        except WitnessProtocolError as exc:
            raise WitnessIntegrityError("witness receipt call descriptor changed") from exc
        if body.get("call_id") != _call_id(
            self.run_id,
            phase=descriptor.phase,
            schedule_index=descriptor.schedule_index,
            attempt_number=descriptor.attempt_number,
        ):
            raise WitnessIntegrityError("witness receipt call identity changed")
        if not isinstance(body.get("witnessed_at"), str) or not body["witnessed_at"]:
            raise WitnessIntegrityError("witness receipt timestamp changed")

    def apply_receipt(
        self, state: _ReplayState, receipt: dict[str, Any], *, sequence: int
    ) -> None:
        self.verifier.require_valid_receipt(receipt)
        body = receipt["body"]
        self._require_body_shape(body, sequence=sequence, previous_head=state.head)
        descriptor = self.descriptor_from_body(body)
        event = body.get("event")
        if event == "call_opened":
            if state.state != "active":
                raise WitnessIntegrityError("witness chain opens a call from invalid state")
            expected_item = self.schedule[state.schedule_position]
            if descriptor != CallDescriptor(
                phase=expected_item.phase,
                schedule_index=expected_item.schedule_index,
                attempt_number=state.attempt_number,
                call_commitment=expected_item.call_commitment,
            ):
                raise WitnessIntegrityError("witness chain diverges from the sealed schedule")
            if any(
                body.get(key) is not None
                for key in (
                    "opened_receipt_sha256",
                    "outcome",
                    "artifact_root_commitment",
                    "token_usage",
                )
            ):
                raise WitnessIntegrityError("witness open receipt contains close fields")
            state.state = "open"
            state.open_receipt = receipt
            state.model_calls_reserved += 1
        elif event == "call_closed":
            if state.state != "open" or state.open_receipt is None:
                raise WitnessIntegrityError("witness chain closes a call without an open")
            opened_sha256 = receipt_sha256(state.open_receipt)
            if (
                body.get("opened_receipt_sha256") != opened_sha256
                or descriptor
                != self.descriptor_from_body(state.open_receipt["body"])
            ):
                raise WitnessIntegrityError("witness close receipt changed its open binding")
            outcome = body.get("outcome")
            if outcome not in OUTCOMES:
                raise WitnessIntegrityError("witness close outcome is invalid")
            try:
                _require_hex64(
                    body.get("artifact_root_commitment"),
                    "artifact root commitment",
                )
                usage = _validate_token_usage(body.get("token_usage"))
            except WitnessProtocolError as exc:
                raise WitnessIntegrityError("witness close economics changed") from exc
            try:
                _validate_outcome_token_usage(outcome, usage)
            except WitnessProtocolError as exc:
                raise WitnessIntegrityError(
                    "witness outcome and token usage disagree"
                ) from exc
            state.model_calls_closed += 1
            state.open_receipt = None
            item = self.schedule[state.schedule_position]
            if outcome == "accepted":
                state.schedule_position += 1
                state.attempt_number = 1
                state.state = (
                    "complete"
                    if state.schedule_position == len(self.schedule)
                    else "active"
                )
            elif outcome == "provider_failure":
                state.attempt_number += 1
                state.state = (
                    "aborted"
                    if state.attempt_number > item.max_attempts
                    else "active"
                )
            else:
                state.state = "aborted"
        else:
            raise WitnessIntegrityError("witness receipt event is invalid")
        state.head = receipt_sha256(receipt)
        state.receipts.append(receipt)

    def _verify_state(
        self,
        receipts: Sequence[dict[str, Any]],
        *,
        expected_head: str | None = None,
    ) -> _ReplayState:
        state = _ReplayState(
            state="active",
            schedule_position=0,
            attempt_number=1,
            head=GENESIS_HEAD,
            receipts=[],
            open_receipt=None,
            model_calls_reserved=0,
            model_calls_closed=0,
        )
        for sequence, receipt in enumerate(receipts):
            if not isinstance(receipt, dict):
                raise WitnessIntegrityError("witness event is not an object")
            self.apply_receipt(state, receipt, sequence=sequence)
        if expected_head is not None:
            _require_hex64(expected_head, "expected witness head")
            if state.head != expected_head:
                raise WitnessIntegrityError(
                    "witness chain rolled back or differs from the expected head"
                )
        return state

    def verify(
        self,
        receipts: Sequence[dict[str, Any]],
        *,
        expected_head: str | None = None,
    ) -> dict[str, Any]:
        state = self._verify_state(receipts, expected_head=expected_head)
        return {
            "run_id": self.run_id,
            "witness_key_id": self.verifier.key_id,
            "head": state.head,
            "events": len(state.receipts),
            "state": state.state,
            "schedule_position": state.schedule_position,
            "next_attempt_number": state.attempt_number,
            "model_calls_reserved": state.model_calls_reserved,
            "model_calls_closed": state.model_calls_closed,
        }


class WitnessLedger:
    """Persist and enforce one signed, hash-chained experiment call inventory."""

    def __init__(
        self,
        root: Path,
        *,
        run_id: str,
        schedule: Sequence[ScheduleItem],
        authenticator: SshEd25519Authenticator,
        clock: Callable[[], str],
    ) -> None:
        self.root = Path(os.path.abspath(root))
        self.run_id = _require_hex64(run_id, "run id")
        self.schedule = _validate_schedule(schedule)
        self.authenticator = authenticator
        self.chain_verifier = WitnessChainVerifier(
            run_id=self.run_id,
            schedule=self.schedule,
            verifier=authenticator,
        )
        self.clock = clock
        self.events_dir = self.root / "events"
        self.head_path = self.root / "HEAD"
        self.lock_path = self.root / ".lock"
        self.config_path = self.root / "run.json"
        self._cached_state: _ReplayState | None = None
        self._cached_inventory: tuple[tuple[str, int, int, int, int], ...] | None = None
        self._initialize_storage()

    def _run_config(self) -> dict[str, Any]:
        return {
            "kind": "experiment_witness_run",
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": self.run_id,
            "witness_key_id": self.authenticator.key_id,
            "witness_identity": self.authenticator.identity,
            "witness_public_key": self.authenticator.public_key,
            "schedule": [asdict(item) for item in self.schedule],
        }

    def _initialize_storage(self) -> None:
        if self.root.is_symlink():
            raise WitnessIntegrityError("witness ledger root is a symlink")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._require_private_directory(self.root, "witness ledger root")
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.lock_path, flags, 0o600)
        except OSError as exc:
            raise WitnessIntegrityError("witness lock is unavailable") from exc
        try:
            self._require_private_regular_descriptor(
                descriptor, self.lock_path, "witness lock"
            )
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self.events_dir.mkdir(exist_ok=True, mode=0o700)
            self._require_private_directory(
                self.events_dir, "witness event storage"
            )
            expected = canonical_json_bytes(self._run_config()) + b"\n"
            if self.config_path.exists():
                config_status = self.config_path.lstat()
                if (
                    self.config_path.is_symlink()
                    or not self.config_path.is_file()
                    or config_status.st_uid != os.geteuid()
                    or stat.S_IMODE(config_status.st_mode) != 0o400
                    or self.config_path.read_bytes() != expected
                ):
                    raise WitnessIntegrityError("witness run configuration changed")
            else:
                self._publish_exclusive(
                    self.config_path, expected, mode=0o400
                )
        finally:
            os.close(descriptor)

    @staticmethod
    def _require_private_directory(path: Path, label: str) -> None:
        try:
            status = path.lstat()
        except OSError as exc:
            raise WitnessIntegrityError(f"{label} is unavailable") from exc
        if (
            not stat.S_ISDIR(status.st_mode)
            or status.st_uid != os.geteuid()
            or stat.S_IMODE(status.st_mode) != 0o700
        ):
            raise WitnessIntegrityError(
                f"{label} must be owned by the witness and mode 0700"
            )

    @staticmethod
    def _require_private_regular_descriptor(
        descriptor: int, path: Path, label: str
    ) -> None:
        descriptor_status = os.fstat(descriptor)
        try:
            path_status = path.lstat()
        except OSError as exc:
            raise WitnessIntegrityError(f"{label} is unavailable") from exc
        if (
            not stat.S_ISREG(descriptor_status.st_mode)
            or stat.S_IMODE(descriptor_status.st_mode) != 0o600
            or descriptor_status.st_uid != os.geteuid()
            or (descriptor_status.st_dev, descriptor_status.st_ino)
            != (path_status.st_dev, path_status.st_ino)
        ):
            raise WitnessIntegrityError(
                f"{label} must be a private witness-owned regular file"
            )

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("witness durable write made no progress")
            offset += written

    @classmethod
    def _write_exclusive(cls, path: Path, payload: bytes, *, mode: int) -> None:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, mode)
        try:
            cls._write_all(descriptor, payload)
            os.fsync(descriptor)
            os.fchmod(descriptor, mode)
        finally:
            os.close(descriptor)

    @classmethod
    def _publish_exclusive(cls, path: Path, payload: bytes, *, mode: int) -> None:
        """Durably stage and atomically publish a never-replaced path."""

        temp = path.parent / f".tmp-{path.name}-{uuid.uuid4().hex}"
        try:
            cls._write_exclusive(temp, payload, mode=mode)
            os.link(temp, path, follow_symlinks=False)
            os.unlink(temp)
            cls._fsync_dir(path.parent)
        except BaseException:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

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

    def _write_head(self, head: str) -> None:
        _require_hex64(head, "witness head")
        temp = self.root / f".HEAD.tmp-{uuid.uuid4().hex}"
        self._write_exclusive(temp, (head + "\n").encode("ascii"), mode=0o600)
        os.replace(temp, self.head_path)
        self._fsync_dir(self.root)

    def _event_paths(self) -> list[Path]:
        children = sorted(self.events_dir.iterdir(), key=lambda path: path.name)
        committed: list[Path] = []
        for path in children:
            if path.name.startswith(".tmp-"):
                if path.is_symlink() or not path.is_file():
                    raise WitnessIntegrityError(
                        "witness event staging inventory is invalid"
                    )
                path.unlink()
                continue
            committed.append(path)
        children = committed
        if any(
            path.is_symlink()
            or not path.is_file()
            or path.stat(follow_symlinks=False).st_uid != os.geteuid()
            or stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) != 0o400
            or _EVENT_NAME.fullmatch(path.name) is None
            for path in children
        ):
            raise WitnessIntegrityError("witness event inventory contains an invalid path")
        for sequence, path in enumerate(children):
            if path.name != f"{sequence:020d}.json":
                raise WitnessIntegrityError("witness event sequence is not contiguous")
        return children

    @staticmethod
    def _inventory(paths: Sequence[Path]) -> tuple[tuple[str, int, int, int, int], ...]:
        result = []
        for path in paths:
            stat_result = path.stat(follow_symlinks=False)
            result.append(
                (
                    path.name,
                    stat_result.st_dev,
                    stat_result.st_ino,
                    stat_result.st_size,
                    stat_result.st_mtime_ns,
                )
            )
        return tuple(result)

    @staticmethod
    def _descriptor_from_body(body: Mapping[str, Any]) -> CallDescriptor:
        return WitnessChainVerifier.descriptor_from_body(body)

    def _apply_receipt(
        self, state: _ReplayState, receipt: dict[str, Any], *, sequence: int
    ) -> None:
        self.chain_verifier.apply_receipt(state, receipt, sequence=sequence)

    def _replay(self) -> _ReplayState:
        paths = self._event_paths()
        receipts: list[dict[str, Any]] = []
        for path in paths:
            try:
                raw = path.read_bytes()
                receipt = json.loads(raw)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WitnessIntegrityError("witness event is not valid JSON") from exc
            if not isinstance(receipt, dict):
                raise WitnessIntegrityError("witness event is not an object")
            if raw != canonical_json_bytes(receipt) + b"\n":
                raise WitnessIntegrityError("witness event is not canonical JSON")
            receipts.append(receipt)
        state = self.chain_verifier._verify_state(receipts)
        chain_heads = [GENESIS_HEAD, *(receipt_sha256(item) for item in receipts)]
        if self.head_path.exists():
            head_status = self.head_path.lstat()
            if (
                self.head_path.is_symlink()
                or not self.head_path.is_file()
                or head_status.st_uid != os.geteuid()
                or stat.S_IMODE(head_status.st_mode) != 0o600
            ):
                raise WitnessIntegrityError("witness HEAD storage is invalid")
            try:
                cached_head = self.head_path.read_text(encoding="ascii").strip()
            except (OSError, UnicodeError) as exc:
                raise WitnessIntegrityError("witness HEAD is unreadable") from exc
            if cached_head not in chain_heads:
                raise WitnessIntegrityError("witness HEAD differs from the signed chain")
            if cached_head != state.head:
                # A crash after the durable event append but before HEAD replace
                # leaves a strict signed-chain prefix. HEAD is only a cache.
                self._write_head(state.head)
        else:
            self._write_head(state.head)
        self._cached_state = copy.deepcopy(state)
        self._cached_inventory = self._inventory(paths)
        return state

    def _transition_state(self) -> _ReplayState:
        """Use the verified in-memory state only while its disk inventory matches."""

        if self._cached_state is None or self._cached_inventory is None:
            return self._replay()
        paths = self._event_paths()
        if self._inventory(paths) != self._cached_inventory:
            return self._replay()
        if not self.head_path.is_file():
            return self._replay()
        try:
            head = self.head_path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return self._replay()
        if head != self._cached_state.head:
            return self._replay()
        return copy.deepcopy(self._cached_state)

    def _validated_next_state(
        self, state: _ReplayState, receipt: dict[str, Any], *, sequence: int
    ) -> _ReplayState:
        next_state = copy.deepcopy(state)
        self._apply_receipt(next_state, receipt, sequence=sequence)
        return next_state

    def _cache_published_state(self, state: _ReplayState) -> None:
        self._cached_state = copy.deepcopy(state)
        self._cached_inventory = self._inventory(self._event_paths())

    def _signed_receipt(self, body: dict[str, Any]) -> dict[str, Any]:
        body_sha256 = sha256_bytes(canonical_json_bytes(body))
        receipt = {
            "kind": "experiment_witness_receipt",
            "schema_version": SCHEMA_VERSION,
            "body": body,
            "body_sha256": body_sha256,
            "signature": self.authenticator.sign_body(body),
        }
        self.authenticator.require_valid_receipt(receipt)
        return receipt

    def _append_receipt(self, receipt: dict[str, Any], *, sequence: int) -> None:
        path = self.events_dir / f"{sequence:020d}.json"
        self._publish_exclusive(
            path,
            canonical_json_bytes(receipt) + b"\n",
            mode=0o400,
        )
        self._write_head(receipt_sha256(receipt))

    @contextmanager
    def _locked(self):
        flags = os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.lock_path, flags)
        except OSError as exc:
            raise WitnessIntegrityError("witness lock is unavailable") from exc
        try:
            self._require_private_regular_descriptor(
                descriptor, self.lock_path, "witness lock"
            )
            with os.fdopen(descriptor, "r+b", closefd=False) as handle:
                yield handle
        finally:
            os.close(descriptor)

    def status(self) -> dict[str, Any]:
        with self._locked() as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = self._replay()
        return {
            "run_id": self.run_id,
            "witness_key_id": self.authenticator.key_id,
            "head": state.head,
            "events": len(state.receipts),
            "state": state.state,
            "schedule_position": state.schedule_position,
            "next_attempt_number": state.attempt_number,
            "model_calls_reserved": state.model_calls_reserved,
            "model_calls_closed": state.model_calls_closed,
        }

    def receipts(self) -> tuple[dict[str, Any], ...]:
        """Return the fully replay-verified signed chain for external retention."""

        with self._locked() as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = self._replay()
        return tuple(copy.deepcopy(state.receipts))

    def open_call(
        self, descriptor: CallDescriptor, *, expected_head: str
    ) -> dict[str, Any]:
        _validate_call_descriptor(descriptor)
        _require_hex64(expected_head, "expected witness head")
        with self._locked() as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = self._transition_state()
            if state.state == "open" and state.open_receipt is not None:
                open_body = state.open_receipt["body"]
                if (
                    expected_head == open_body["prev_receipt_sha256"]
                    and descriptor == self._descriptor_from_body(open_body)
                ):
                    return state.open_receipt
                raise WitnessProtocolError("witness has an unresolved open call")
            if state.state == "aborted":
                raise WitnessProtocolError("witness run is aborted")
            if state.state == "complete":
                raise WitnessProtocolError("witness run is complete")
            if expected_head != state.head:
                raise WitnessProtocolError("expected witness head is stale or changed")
            item = self.schedule[state.schedule_position]
            expected = CallDescriptor(
                phase=item.phase,
                schedule_index=item.schedule_index,
                attempt_number=state.attempt_number,
                call_commitment=item.call_commitment,
            )
            if descriptor != expected:
                raise WitnessProtocolError("call differs from the sealed witness schedule")
            body = {
                "witness_key_id": self.authenticator.key_id,
                "run_id": self.run_id,
                "seq": len(state.receipts),
                "prev_receipt_sha256": state.head,
                "event": "call_opened",
                "phase": descriptor.phase,
                "schedule_index": descriptor.schedule_index,
                "attempt_number": descriptor.attempt_number,
                "call_id": _call_id(
                    self.run_id,
                    phase=descriptor.phase,
                    schedule_index=descriptor.schedule_index,
                    attempt_number=descriptor.attempt_number,
                ),
                "call_commitment": descriptor.call_commitment,
                "opened_receipt_sha256": None,
                "outcome": None,
                "artifact_root_commitment": None,
                "token_usage": None,
                "witnessed_at": self.clock(),
            }
            receipt = self._signed_receipt(body)
            next_state = self._validated_next_state(
                state, receipt, sequence=len(state.receipts)
            )
            self._append_receipt(receipt, sequence=len(state.receipts))
            self._cache_published_state(next_state)
            return receipt

    def close_call(
        self,
        *,
        opened_receipt_sha256: str,
        outcome: str,
        artifact_root_commitment: str,
        token_usage: Mapping[str, Any],
        expected_head: str,
    ) -> dict[str, Any]:
        _require_hex64(opened_receipt_sha256, "opened receipt digest")
        if outcome not in OUTCOMES:
            raise WitnessProtocolError("witness call outcome is invalid")
        _require_hex64(artifact_root_commitment, "artifact root commitment")
        usage = _validate_token_usage(token_usage)
        _validate_outcome_token_usage(outcome, usage)
        _require_hex64(expected_head, "expected witness head")
        with self._locked() as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = self._transition_state()
            if state.receipts and state.receipts[-1]["body"]["event"] == "call_closed":
                prior = state.receipts[-1]
                prior_body = prior["body"]
                if prior_body["opened_receipt_sha256"] == opened_receipt_sha256:
                    same = (
                        expected_head == prior_body["prev_receipt_sha256"]
                        and prior_body["outcome"] == outcome
                        and prior_body["artifact_root_commitment"]
                        == artifact_root_commitment
                        and prior_body["token_usage"] == usage
                    )
                    if same:
                        return prior
                    raise WitnessProtocolError("conflicting close for witnessed call")
            if state.state != "open" or state.open_receipt is None:
                raise WitnessProtocolError("witness has no unresolved call to close")
            if expected_head != state.head:
                raise WitnessProtocolError("expected witness head is stale or changed")
            if opened_receipt_sha256 != state.head:
                raise WitnessProtocolError("close does not bind the unresolved open call")
            descriptor = self._descriptor_from_body(state.open_receipt["body"])
            body = {
                "witness_key_id": self.authenticator.key_id,
                "run_id": self.run_id,
                "seq": len(state.receipts),
                "prev_receipt_sha256": state.head,
                "event": "call_closed",
                "phase": descriptor.phase,
                "schedule_index": descriptor.schedule_index,
                "attempt_number": descriptor.attempt_number,
                "call_id": _call_id(
                    self.run_id,
                    phase=descriptor.phase,
                    schedule_index=descriptor.schedule_index,
                    attempt_number=descriptor.attempt_number,
                ),
                "call_commitment": descriptor.call_commitment,
                "opened_receipt_sha256": opened_receipt_sha256,
                "outcome": outcome,
                "artifact_root_commitment": artifact_root_commitment,
                "token_usage": usage,
                "witnessed_at": self.clock(),
            }
            receipt = self._signed_receipt(body)
            next_state = self._validated_next_state(
                state, receipt, sequence=len(state.receipts)
            )
            self._append_receipt(receipt, sequence=len(state.receipts))
            self._cache_published_state(next_state)
            return receipt
