#!/usr/bin/env python3
"""Build a content-only provenance receipt for an rsynced source tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "codex-source-provenance-v1"


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL
    )


def _source_entries(repo: Path) -> list[dict[str, Any]]:
    paths = _git(
        repo,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
    ).split(b"\0")
    entries: list[dict[str, Any]] = []
    for encoded_path in paths:
        if not encoded_path:
            continue
        relative = encoded_path.decode("utf-8", errors="strict")
        path = repo / relative
        try:
            status = path.lstat()
        except FileNotFoundError:
            # `git ls-files --cached` includes tracked paths deleted from the
            # working tree. Their absence is represented by the dirty flag and
            # by omission from the current-tree manifest.
            continue
        if stat.S_ISLNK(status.st_mode):
            payload = os.readlink(path).encode("utf-8")
            kind = "symlink"
        elif stat.S_ISREG(status.st_mode):
            payload = path.read_bytes()
            kind = "file"
        else:
            raise ValueError(f"source tree contains unsupported entry: {relative}")
        entries.append(
            {
                "bytes": len(payload),
                "executable": bool(status.st_mode & stat.S_IXUSR),
                "kind": kind,
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return sorted(entries, key=lambda entry: entry["path"])


def build_receipt(repo: Path) -> dict[str, Any]:
    root = repo.resolve(strict=True)
    commit = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
    dirty = bool(_git(root, "status", "--porcelain=v1", "-z"))
    manifest = json.dumps(
        _source_entries(root),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    return {
        "schema_version": SCHEMA_VERSION,
        "source_commit": commit,
        "source_dirty": dirty,
        "source_manifest_sha256": hashlib.sha256(manifest).hexdigest(),
    }


def _canonical_line(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = _canonical_line(build_receipt(args.repo))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("xb") as handle:
        handle.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
