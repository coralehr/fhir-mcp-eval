#!/usr/bin/env python3
"""Production Codex driver for the trusted experiment executor.

The driver accepts only an already-sealed invocation.  It executes the pinned
native binary directly with a fixed command and environment, writes the three
raw capture files, and returns transport termination metadata.  It never
derives outcome, retry eligibility, token usage, or artifact commitments.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import resource
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from experiment_executor import (
    DriverTermination,
    ExecutorProtocolError,
    SealedInvocation,
)


class TrustedDriverError(RuntimeError):
    """The trusted runtime configuration or capture surface is unsafe."""


class TrustedCodexDriver:
    """Invoke a sealed native Codex runtime without ambient configuration."""

    _SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
    _MACH_O_MAGICS = {
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xce",
        b"\xcf\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
    }
    _PROCESS_GROUP_DRAIN_SECONDS = 5.0
    _MAX_CREDENTIAL_BYTES = 4 * 1024 * 1024
    _MAX_CAPTURE_BYTES = 64 * 1024 * 1024
    _SANDBOX_PROFILE = "(version 1)(allow default)(deny process-fork)"

    def __init__(
        self,
        *,
        account_home: Path,
        codex_home: Path,
        scratch_root: Path,
        sandbox_exec_path: Path,
        sandbox_exec_sha256: str,
    ) -> None:
        self.account_home = Path(os.path.abspath(account_home))
        self.codex_home = Path(os.path.abspath(codex_home))
        self.scratch_root = Path(os.path.abspath(scratch_root))
        self.sandbox_exec_path = Path(os.path.abspath(sandbox_exec_path))
        self.sandbox_exec_sha256 = sandbox_exec_sha256
        self._require_owned_directory(
            self.account_home, "executor account home", exact_private=False
        )
        self._require_owned_directory(
            self.codex_home, "trusted Codex home", exact_private=True
        )
        self._require_owned_directory(
            self.scratch_root, "trusted driver scratch root", exact_private=True
        )
        self._require_private_credential(self.codex_home / "auth.json")
        self._require_sandbox_exec()

    def _require_sandbox_exec(self) -> None:
        if (
            not isinstance(self.sandbox_exec_sha256, str)
            or len(self.sandbox_exec_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.sandbox_exec_sha256
            )
        ):
            raise TrustedDriverError("sandbox executable digest is invalid")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.sandbox_exec_path, flags)
            try:
                status = os.fstat(descriptor)
                path_status = self.sandbox_exec_path.lstat()
                digest = hashlib.sha256()
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise TrustedDriverError("sandbox executable is unavailable") from exc
        if (
            self.sandbox_exec_path.is_symlink()
            or not stat.S_ISREG(status.st_mode)
            or status.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(status.st_mode) & 0o022
            or not stat.S_IMODE(status.st_mode) & 0o100
            or status.st_nlink != 1
            or (status.st_dev, status.st_ino)
            != (path_status.st_dev, path_status.st_ino)
            or digest.hexdigest() != self.sandbox_exec_sha256
        ):
            raise TrustedDriverError("sandbox executable identity changed")

    @staticmethod
    def _require_owned_directory(
        path: Path, label: str, *, exact_private: bool
    ) -> None:
        try:
            status = path.lstat()
        except OSError as exc:
            raise TrustedDriverError(f"{label} is unavailable") from exc
        mode = stat.S_IMODE(status.st_mode)
        if (
            not stat.S_ISDIR(status.st_mode)
            or path.is_symlink()
            or status.st_uid != os.geteuid()
            or mode & 0o022
            or (exact_private and mode != 0o700)
        ):
            raise TrustedDriverError(f"{label} is not executor-owned and private")

    @staticmethod
    def _require_private_credential(path: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
            try:
                status = os.fstat(descriptor)
                path_status = path.lstat()
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise TrustedDriverError("trusted Codex credential is unavailable") from exc
        if (
            path.is_symlink()
            or not stat.S_ISREG(status.st_mode)
            or status.st_uid != os.geteuid()
            or stat.S_IMODE(status.st_mode) != 0o600
            or status.st_nlink != 1
            or (status.st_dev, status.st_ino)
            != (path_status.st_dev, path_status.st_ino)
        ):
            raise TrustedDriverError(
                "trusted Codex credential is not an executor-owned mode-0600 file"
            )

    @classmethod
    def _credential_markers(cls, path: Path) -> tuple[bytes, ...]:
        cls._require_private_credential(path)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            status = os.fstat(descriptor)
            if status.st_size > cls._MAX_CREDENTIAL_BYTES:
                raise TrustedDriverError("trusted Codex credential exceeds byte cap")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > cls._MAX_CREDENTIAL_BYTES:
                    raise TrustedDriverError("trusted Codex credential exceeds byte cap")
                chunks.append(chunk)
        finally:
            os.close(descriptor)
        raw = b"".join(chunks)
        markers = {raw} if raw else set()
        try:
            parsed = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None

        def collect(value: object, *, secret_context: bool = False) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    key_is_secret = secret_context or any(
                        word in str(key).lower()
                        for word in ("token", "secret", "credential", "api_key")
                    )
                    collect(child, secret_context=key_is_secret)
            elif isinstance(value, list):
                for child in value:
                    collect(child, secret_context=secret_context)
            elif secret_context and isinstance(value, str) and len(value) >= 16:
                markers.add(value.encode("utf-8"))

        collect(parsed)
        return tuple(sorted(markers, key=lambda value: (len(value), value)))

    @classmethod
    def _require_native_runtime(cls, path: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
            try:
                status = os.fstat(descriptor)
                path_status = path.lstat()
                magic = os.read(descriptor, 4)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise TrustedDriverError("sealed native runtime is unavailable") from exc
        if (
            path.is_symlink()
            or not stat.S_ISREG(status.st_mode)
            or status.st_uid != os.geteuid()
            or stat.S_IMODE(status.st_mode) & 0o022
            or not stat.S_IMODE(status.st_mode) & 0o100
            or status.st_nlink != 1
            or (status.st_dev, status.st_ino)
            != (path_status.st_dev, path_status.st_ino)
            or magic not in cls._MACH_O_MAGICS
        ):
            raise TrustedDriverError(
                "sealed runtime is not one executor-owned native Mach-O executable"
            )

    @staticmethod
    def _require_capture_directory(path: Path) -> None:
        TrustedCodexDriver._require_owned_directory(
            path, "raw capture directory", exact_private=True
        )
        try:
            if any(path.iterdir()):
                raise TrustedDriverError("raw capture directory is not empty")
        except OSError as exc:
            raise TrustedDriverError("raw capture directory is unavailable") from exc

    @staticmethod
    def _write_private(path: Path, payload: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("private write made no progress")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _open_private_capture(path: Path) -> object:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        return os.fdopen(descriptor, "wb", buffering=0)

    @staticmethod
    def _fsync_capture_file(path: Path) -> None:
        flags = os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
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
                raise TrustedDriverError("raw capture file identity changed")
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _process_group_exists(process_group: int) -> bool:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @classmethod
    def _wait_for_process_group_exit(cls, process_group: int) -> None:
        deadline = time.monotonic() + cls._PROCESS_GROUP_DRAIN_SECONDS
        while cls._process_group_exists(process_group):
            if time.monotonic() >= deadline:
                raise TrustedDriverError("Codex process group did not terminate")
            time.sleep(0.01)

    @classmethod
    def _terminate_process_group(cls, process: object) -> None:
        process_group = process.pid
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=cls._PROCESS_GROUP_DRAIN_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise TrustedDriverError("Codex process leader did not terminate") from exc
        cls._wait_for_process_group_exit(process_group)

    @staticmethod
    def _apply_child_limits(byte_cap: int) -> None:
        """Install the per-file capture cap in the isolated child pre-exec."""
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (byte_cap, byte_cap),
        )

    @staticmethod
    def _capture_contains_marker(
        paths: tuple[Path, ...], markers: tuple[bytes, ...]
    ) -> bool:
        for path in paths:
            if not path.exists():
                continue
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags)
            try:
                status = os.fstat(descriptor)
                path_status = path.lstat()
                if (
                    not stat.S_ISREG(status.st_mode)
                    or status.st_uid != os.geteuid()
                    or status.st_size > TrustedCodexDriver._MAX_CAPTURE_BYTES
                    or status.st_nlink != 1
                    or (status.st_dev, status.st_ino)
                    != (path_status.st_dev, path_status.st_ino)
                ):
                    raise TrustedDriverError("raw capture file identity changed")
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
            finally:
                os.close(descriptor)
            raw = b"".join(chunks)
            if any(marker and marker in raw for marker in markers):
                return True
        return False

    @staticmethod
    def _scrub_capture_files(paths: tuple[Path, ...]) -> None:
        parents = {path.parent for path in paths}
        if len(parents) != 1:
            raise TrustedDriverError("raw capture paths have different parents")
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise TrustedDriverError("credential-bearing capture cannot be scrubbed") from exc
        TrustedCodexDriver._fsync_directory(next(iter(parents)))

    def _fixed_command(
        self,
        invocation: SealedInvocation,
        *,
        schema_path: Path,
        answer_path: Path,
        working_directory: Path,
    ) -> list[str]:
        if invocation.reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            raise ExecutorProtocolError("sealed invocation reasoning is invalid")
        return [
            str(self.sandbox_exec_path),
            "-p",
            self._SANDBOX_PROFILE,
            invocation.runtime_path,
            "-a",
            "never",
            "--strict-config",
            "-c",
            'shell_environment_policy.inherit="none"',
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--json",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(answer_path),
            "-C",
            str(working_directory),
            "-s",
            "read-only",
            "--color",
            "never",
            "-c",
            f"model_reasoning_effort={json.dumps(invocation.reasoning_effort)}",
            "-m",
            invocation.model,
            "-",
        ]

    def invoke(
        self, invocation: SealedInvocation, capture_dir: Path
    ) -> DriverTermination:
        capture_dir = Path(os.path.abspath(capture_dir))
        self._require_owned_directory(
            self.account_home, "executor account home", exact_private=False
        )
        self._require_owned_directory(
            self.codex_home, "trusted Codex home", exact_private=True
        )
        self._require_owned_directory(
            self.scratch_root, "trusted driver scratch root", exact_private=True
        )
        self._require_sandbox_exec()
        credential_path = self.codex_home / "auth.json"
        credential_markers = self._credential_markers(credential_path)
        self._require_capture_directory(capture_dir)
        runtime_path = Path(invocation.runtime_path)
        if not runtime_path.is_absolute():
            raise TrustedDriverError("sealed native runtime path is not absolute")
        self._require_native_runtime(runtime_path)

        with tempfile.TemporaryDirectory(
            prefix="codex-driver-", dir=self.scratch_root
        ) as temporary:
            temporary_root = Path(temporary)
            temporary_root.chmod(0o700)
            working_directory = temporary_root / "work"
            working_directory.mkdir(mode=0o700)
            temporary_directory = temporary_root / "tmp"
            temporary_directory.mkdir(mode=0o700)
            schema_path = temporary_root / "schema.json"
            self._write_private(schema_path, invocation.output_schema)

            answer_path = capture_dir / "answer.json"
            event_path = capture_dir / "events.jsonl"
            stderr_path = capture_dir / "stderr.log"
            command = self._fixed_command(
                invocation,
                schema_path=schema_path,
                answer_path=answer_path,
                working_directory=working_directory,
            )
            environment = {
                "CODEX_HOME": str(self.codex_home),
                "HOME": str(self.account_home),
                "LANG": "en_US.UTF-8",
                "LC_ALL": "en_US.UTF-8",
                "NO_COLOR": "1",
                "PATH": self._SYSTEM_PATH,
                "TMPDIR": str(temporary_directory),
            }

            timed_out = False
            process: object | None = None
            pending_error: BaseException | None = None
            try:
                with (
                    self._open_private_capture(event_path) as stdout,
                    self._open_private_capture(stderr_path) as stderr,
                ):
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE,
                        stdout=stdout,
                        stderr=stderr,
                        cwd=str(working_directory),
                        env=environment,
                        shell=False,
                        start_new_session=True,
                        umask=0o077,
                        preexec_fn=functools.partial(
                            self._apply_child_limits, self._MAX_CAPTURE_BYTES
                        ),
                    )
                    try:
                        process.communicate(
                            input=invocation.prompt,
                            timeout=invocation.timeout_seconds,
                        )
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        self._terminate_process_group(process)
                    except BaseException:
                        self._terminate_process_group(process)
                        raise

                if not timed_out and self._process_group_exists(process.pid):
                    self._terminate_process_group(process)
                    raise TrustedDriverError(
                        "Codex left a surviving process-group member"
                    )

                if type(process.returncode) is not int:
                    raise TrustedDriverError(
                        "Codex process has no terminal exit status"
                    )
            except BaseException as exc:
                pending_error = exc

            capture_paths = (event_path, stderr_path, answer_path)
            try:
                credential_markers = tuple(
                    set(credential_markers)
                    | set(self._credential_markers(credential_path))
                )
                credential_found = self._capture_contains_marker(
                    capture_paths, credential_markers
                )
            except BaseException:
                self._scrub_capture_files(capture_paths)
                raise
            if credential_found:
                self._scrub_capture_files(capture_paths)
                raise TrustedDriverError("Codex capture contains credential material")
            for path in capture_paths:
                if path.exists():
                    self._fsync_capture_file(path)
            self._fsync_directory(capture_dir)
            if pending_error is not None:
                raise pending_error
            if process is None:
                raise TrustedDriverError("Codex process did not start")
            return DriverTermination(
                exit_code=process.returncode,
                timed_out=timed_out,
                runtime_sha256=invocation.runtime_sha256,
            )
