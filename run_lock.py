"""Cross-process singleton locks for model-facing experiment runners."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import TextIO


LOCK_BUSY_EXIT = 75


class AlreadyRunning(RuntimeError):
    """Raised when another process already holds the requested runner lock."""


class InstanceLock:
    def __init__(self, path: Path, handle: TextIO) -> None:
        self.path = path
        self._handle = handle

    def close(self) -> None:
        if self._handle.closed:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()

    def __enter__(self) -> InstanceLock:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def acquire_single_instance(path: Path) -> InstanceLock:
    """Acquire a non-blocking exclusive lock and record the owning PID."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.seek(0)
        owner = handle.read().strip() or "unknown PID"
        handle.close()
        raise AlreadyRunning(f"{path} is held by {owner}") from exc

    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    return InstanceLock(path, handle)
