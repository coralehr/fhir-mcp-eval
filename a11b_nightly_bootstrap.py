#!/usr/bin/env python3
"""Fixed isolated bootstrap for the sealed A11b overnight runner."""

from __future__ import annotations

import os
import resource
import runpy
import sys
from pathlib import Path


BUNDLE_DIR = Path("/Library/Application Support/CoralEHR/experiment-executor")
CODE_DIR = Path("/usr/local/lib/coralehr-experiment-executor")
BOOTSTRAP_PATH = CODE_DIR / "a11b_nightly_bootstrap.py"
RUNNER_PATH = CODE_DIR / "a11b_nightly_runner.py"
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
            and all(
                character in "0123456789abcdefABCDEFx:"
                for character in text_encoding
            )
        )
    )


def _prepare_runner() -> None:
    current_umask = os.umask(0o077)
    os.umask(current_umask)
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
        or current_umask != 0o077
        or resource.getrlimit(resource.RLIMIT_CORE) != (0, 0)
    ):
        raise RuntimeError("trusted nightly bootstrap isolation changed")
    sys.path.append(str(CODE_DIR))
    sys.argv[:] = [str(RUNNER_PATH)]


def main() -> int:
    _prepare_runner()
    runpy.run_path(str(RUNNER_PATH), run_name="__main__")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
