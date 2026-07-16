#!/usr/bin/env python3
"""Compile the exact relocatable Python tree receipt for the trusted executor."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Callable

import experiment_executor_install as install


MAX_FILES = 20_000
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MACHO_MAGICS = {
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xce",
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
}
OPTIONAL_EXTERNAL_MACHO = frozenset(
    {
        "lib/itcl4.3.5/libitcl4.3.5.dylib",
        "lib/itcl4.3.5/libtcl9itcl4.3.5.dylib",
        "lib/libpython3.14.dylib",
        "lib/libtcl9.0.dylib",
        "lib/libtcl9tk9.0.dylib",
        "lib/python3.14/lib-dynload/_tkinter.cpython-314-darwin.so",
        "lib/thread3.0.4/libtcl9thread3.0.4.dylib",
        "lib/thread3.0.4/libthread3.0.4.dylib",
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def macho_dependencies(path: Path) -> list[str]:
    process = subprocess.run(
        ["/usr/bin/otool", "-L", str(path)],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        timeout=30,
    )
    if process.returncode != 0:
        raise ValueError(f"cannot inspect Python Mach-O dependency: {path.name}")
    lines = process.stdout.splitlines()
    if not lines or not lines[0].endswith(":"):
        raise ValueError(f"Python Mach-O dependency output changed: {path.name}")
    dependencies = []
    for line in lines[1:]:
        dependency = line.strip().split(" (compatibility version", 1)[0]
        if dependency:
            dependencies.append(dependency)
    return sorted(set(dependencies))


def build_python_tree_receipt(
    source_root: Path,
    *,
    dependency_reader: Callable[[Path], list[str]] = macho_dependencies,
) -> dict[str, object]:
    source_root = Path(os.path.abspath(source_root))
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError("Python source tree is unavailable")
    entries: list[dict[str, object]] = []
    total_bytes = 0
    for path in sorted(source_root.rglob("*"), key=lambda item: item.as_posix()):
        status = path.lstat()
        if stat.S_ISLNK(status.st_mode):
            continue
        if stat.S_ISDIR(status.st_mode):
            continue
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise ValueError("Python tree contains a non-regular entry")
        if status.st_size == 0:
            continue
        if len(entries) >= MAX_FILES or status.st_size > MAX_FILE_BYTES:
            raise ValueError("Python tree exceeds its registered bounds")
        with path.open("rb") as handle:
            magic = handle.read(4)
        is_macho = magic in MACHO_MAGICS
        dependencies = dependency_reader(path) if is_macho else []
        external_dependencies = any(
            not (
                dependency.startswith("/usr/lib/")
                or dependency.startswith("/System/Library/")
                or dependency.startswith(str(install.PRODUCTION_PYTHON_ROOT) + "/")
            )
            for dependency in dependencies
        )
        relative = path.relative_to(source_root).as_posix()
        if external_dependencies and relative in OPTIONAL_EXTERNAL_MACHO:
            continue
        if external_dependencies:
            raise ValueError("Python Mach-O dependency escapes the closed runtime")
        total_bytes += status.st_size
        if total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("Python tree exceeds its registered bounds")
        entries.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "bytes": status.st_size,
                "mode": "0555" if status.st_mode & 0o111 else "0444",
                "owner": "root",
                "group": "wheel",
                "links": 1,
                "format": "macho" if is_macho else "data",
                "dependencies": dependencies,
            }
        )
    if not entries:
        raise ValueError("Python tree is empty")
    receipt = {
        "schema_version": install.PYTHON_TREE_SCHEMA_VERSION,
        "root": str(install.PRODUCTION_PYTHON_ROOT),
        "executable": str(install.service.PRODUCTION_PYTHON_PATH),
        "tree_sha256": install._python_tree_digest(entries),
        "files": len(entries),
        "bytes": total_bytes,
        "version": install.PINNED_PYTHON_VERSION,
        "entries": entries,
    }
    install._python_runtime_receipt(receipt)
    return receipt


def stage_python_tree(
    source_root: Path,
    destination_root: Path,
    receipt: dict[str, object],
) -> None:
    """Copy exactly the receipt-listed regular files into a fresh tree."""

    install._python_runtime_receipt(receipt)
    source_root = Path(os.path.abspath(source_root))
    destination_root = Path(os.path.abspath(destination_root))
    if destination_root.exists() or destination_root.is_symlink():
        raise FileExistsError(destination_root)
    destination_root.mkdir(mode=0o700, parents=True)
    entries = receipt["entries"]
    assert isinstance(entries, list)
    for entry in entries:
        assert isinstance(entry, dict)
        relative = Path(str(entry["path"]))
        source = source_root / relative
        destination = destination_root / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Python receipt source changed: {relative}")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o500 if entry["mode"] == "0555" else 0o400)
        if (
            destination.stat().st_size != entry["bytes"]
            or sha256_file(destination) != entry["sha256"]
        ):
            raise ValueError(f"staged Python receipt differs: {relative}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage-root", type=Path)
    args = parser.parse_args()
    receipt = build_python_tree_receipt(args.source_root)
    payload = (
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    with args.output.open("xb") as handle:
        handle.write(payload)
    args.output.chmod(0o400)
    if args.stage_root is not None:
        stage_python_tree(args.source_root, args.stage_root, receipt)


if __name__ == "__main__":
    main()
