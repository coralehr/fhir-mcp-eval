#!/usr/bin/env python3
"""Root-owned isolated bootstrap for the restricted experiment service."""

from __future__ import annotations

import os
import resource
import runpy
import stat
import sys
from pathlib import Path


BUNDLE_DIR = Path("/Library/Application Support/CoralEHR/experiment-executor")
CODE_DIR = Path("/usr/local/lib/coralehr-experiment-executor")
BOOTSTRAP_PATH = CODE_DIR / "experiment_executor_bootstrap.py"
SERVICE_PATH = CODE_DIR / "experiment_executor_service.py"
PYTHON_PATH = CODE_DIR / "python/bin/python3.14"
TMPDIR = BUNDLE_DIR / "scratch/service-tmp"
EXPECTED_ENVIRONMENT = {
    "HOME": str(BUNDLE_DIR),
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "TMPDIR": str(TMPDIR),
}
_MACOS_TEXT_ENCODING = "__CF_USER_TEXT_ENCODING"


def _environment_is_exact() -> bool:
    actual = dict(os.environ)
    text_encoding = actual.pop(_MACOS_TEXT_ENCODING, None)
    return actual == EXPECTED_ENVIRONMENT and (
        text_encoding is None
        or (
            text_encoding.startswith("0x")
            and len(text_encoding) <= 32
            and all(character in "0123456789abcdefABCDEFx:" for character in text_encoding)
        )
    )


def _process_limits_are_exact() -> bool:
    current_umask = os.umask(0o077)
    os.umask(current_umask)
    return current_umask == 0o077 and resource.getrlimit(resource.RLIMIT_CORE) == (
        0,
        0,
    )


# Every sealed module the service imports at load time. Kept in lockstep with
# experiment_executor_service._current_code_subjects; the bootstrap cannot
# import the service to discover this set without executing it, which is the
# very thing this gate front-runs.
_SEALED_CODE_FILENAMES = (
    "a11b_launch_protocol.py",
    "codex_harness.py",
    "experiment_anchor.py",
    "experiment_executor.py",
    "experiment_executor_bootstrap.py",
    "experiment_executor_service.py",
    "experiment_witness.py",
    "trusted_codex_driver.py",
)

# Sealed code is installed root-owned (per the install manifest), and the
# service's own detective check (_read_immutable_code_file) requires strictly
# root, so the preventive gate matches it. Overridden only by tests, which
# cannot create root-owned files.
_REQUIRED_FILE_UID = 0


def _ancestor_owner_is_trusted(uid: int) -> bool:
    # CODE_DIR's production ancestors (/usr/local/lib, /usr/local, /usr, /) are
    # all root-owned, so this is root-equivalent in production. Accepting the
    # euid as well keeps the ancestor walk testable under a user-owned tempdir
    # whose own ancestors are root-owned; the run account is a distinct third
    # principal and is excluded either way.
    return uid in {0, os.geteuid()}


def _require_safe_code_ancestors(path: Path) -> None:
    current = path
    while True:
        try:
            status = current.lstat()
        except OSError as exc:
            raise RuntimeError("sealed code ancestor is unavailable") from exc
        if (
            current.is_symlink()
            or not stat.S_ISDIR(status.st_mode)
            or not _ancestor_owner_is_trusted(status.st_uid)
            or stat.S_IMODE(status.st_mode) & 0o022
        ):
            raise RuntimeError("sealed code ancestor is unsafe")
        if current.parent == current:
            break
        current = current.parent


def _require_immutable_sealed_file(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    if absolute.is_symlink() or absolute.suffix != ".py":
        raise RuntimeError("sealed code path is unsafe")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(absolute, flags)
    except OSError as exc:
        raise RuntimeError("sealed code is unavailable") from exc
    try:
        status = os.fstat(descriptor)
        path_status = absolute.lstat()
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_uid != _REQUIRED_FILE_UID
            or stat.S_IMODE(status.st_mode) & 0o222
            or status.st_nlink != 1
            or (status.st_dev, status.st_ino)
            != (path_status.st_dev, path_status.st_ino)
        ):
            raise RuntimeError("sealed code metadata is unsafe")
    finally:
        os.close(descriptor)


def _verify_sealed_source() -> None:
    # Preventive integrity gate: BEFORE runpy imports the service and its
    # siblings, require (a) CODE_DIR and every ancestor is a non-symlink
    # directory owned by a trusted principal with no group/other write, so the
    # run account cannot create/replace/delete anything under it, and (b) every
    # sealed module file is a root-owned, non-writable, single-link regular
    # file — closing in-place content edits of an individually run-account-owned
    # sibling. The service performs the same check on itself, but only AFTER
    # runpy has already executed all of these modules, so the check there is
    # detective, not preventive.
    _require_safe_code_ancestors(CODE_DIR)
    for filename in _SEALED_CODE_FILENAMES:
        _require_immutable_sealed_file(CODE_DIR / filename)


def _prepare_service() -> None:
    if (
        sys.flags.isolated != 1
        or sys.flags.dont_write_bytecode != 1
        or sys.flags.no_site != 1
        or len(sys.argv) != 1
        or Path(sys.executable).resolve() != PYTHON_PATH
        or Path(os.path.abspath(sys.argv[0])) != BOOTSTRAP_PATH
        or Path.cwd() != BUNDLE_DIR
        or not _environment_is_exact()
        or str(CODE_DIR) in sys.path
        or not _process_limits_are_exact()
    ):
        raise RuntimeError("trusted bootstrap isolation changed")
    _verify_sealed_source()
    sys.path.append(str(CODE_DIR))
    sys.argv[:] = [str(SERVICE_PATH)]


def main() -> int:
    _prepare_service()
    runpy.run_path(str(SERVICE_PATH), run_name="__main__")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
