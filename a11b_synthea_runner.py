#!/usr/bin/env python3
"""Stage and run the exact zero-model Synthea generation registered for A11b."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from a11_evidence_core import canonical_bytes, sha256
from a11b_generation_receipt import (
    GENERATION_SPEC_VERSION,
    _load_json,
    _parse_properties,
    _write_new_file,
    compile_generation_receipt,
)
from a11b_power_gate import spec_sha256 as power_spec_sha256
from a11b_power_gate import verify_power_receipt


SYNTHEA_RELEASE = "v4.0.0"
SYNTHEA_COMMIT = "0185c09ea9d10a822c6f5f3ef9bdcbcbe960c813"
SYNTHEA_JAR_SHA256 = "ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1"
SYNTHEA_JAR_BYTES = 201_164_144
JDK_RELEASE = "jdk-21.0.11+10"
JDK_ARCHIVE_SHA256 = "6ebcf221c9b41507b14c098e93c6ead6440b8d9bd154f8ec666c4c73abbdb201"
JAVA_VENDOR = "Temurin"
JAVA_VERSION = "21.0.11"
JAVA_EXECUTABLE = "runtime/Contents/Home/bin/java"
JAVA_PROBE = "runtime/java-version.txt"
CONFIGURATION = "configuration/synthea.properties"
MODULE_ROOT = "modules"
OUTPUT_ROOT = "output"
GENERATION_SEED = 20_260_716
REFERENCE_DATE = "2026-07-15"
REFERENCE_DATE_COMPACT = "20260715"
PROCESS_ENVIRONMENT = {
    "LANG": "en_US.UTF-8",
    "LC_ALL": "en_US.UTF-8",
    "TZ": "UTC",
}
MAX_JDK_ARCHIVE_ENTRIES = 2_000
MAX_JDK_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024


def _file_receipt(path: Path, relative: str) -> dict[str, Any]:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"staged input is a symlink: {relative}")
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError(f"staged input is not a unique regular file: {relative}")
    data = path.read_bytes()
    if len(data) != metadata.st_size:
        raise ValueError(f"staged input changed during read: {relative}")
    return {"path": relative, "sha256": sha256(data), "bytes": len(data)}


def _tree_receipts(root: Path, relative_root: str) -> list[dict[str, Any]]:
    tree = root / relative_root
    metadata = tree.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"staged tree is not a real directory: {relative_root}")
    receipts: list[dict[str, Any]] = []
    for directory, directory_names, file_names in os.walk(tree, followlinks=False):
        directory_path = Path(directory)
        for name in directory_names:
            child = directory_path / name
            child_metadata = child.lstat()
            if stat.S_ISLNK(child_metadata.st_mode) or not stat.S_ISDIR(
                child_metadata.st_mode
            ):
                raise ValueError(f"staged tree contains an unsafe directory: {child}")
        for name in file_names:
            child = directory_path / name
            relative = child.relative_to(root).as_posix()
            receipts.append(_file_receipt(child, relative))
    receipts.sort(key=lambda entry: entry["path"])
    if not receipts:
        raise ValueError(f"staged tree is empty: {relative_root}")
    return receipts


def _power_binding(power_spec: object, power_receipt: object) -> dict[str, Any]:
    verify_power_receipt(power_spec, power_receipt)
    if not isinstance(power_receipt, dict):
        raise ValueError("power receipt is invalid")
    population = power_receipt.get("required_source_patients")
    if type(population) is not int or population != 448:
        raise ValueError("A11b power gate must require exactly 448 source patients")
    return {
        "spec_sha256": power_spec_sha256(power_spec),
        "receipt_sha256": sha256(canonical_bytes(power_receipt)),
        "required_source_patients": population,
    }


def _typed_properties(data: bytes) -> dict[str, Any]:
    properties = _parse_properties(data)
    return {
        key: True if value == "true" else False if value == "false" else value
        for key, value in properties.items()
    }


def build_generation_spec(
    artifact_root: Path,
    *,
    power_spec: object,
    power_receipt: object,
) -> dict[str, Any]:
    """Build the prospective exact-input spec from one staged artifact root."""

    artifact_root = Path(artifact_root)
    binding = _power_binding(power_spec, power_receipt)
    jar = _file_receipt(
        artifact_root / "generator" / "synthea.jar",
        "generator/synthea.jar",
    )
    executable = _file_receipt(artifact_root / JAVA_EXECUTABLE, JAVA_EXECUTABLE)
    if not (artifact_root / JAVA_EXECUTABLE).stat().st_mode & 0o111:
        raise ValueError("staged Java executable is not executable")
    probe = _file_receipt(artifact_root / JAVA_PROBE, JAVA_PROBE)
    probe_text = (artifact_root / JAVA_PROBE).read_text(encoding="utf-8")
    if JAVA_VENDOR not in probe_text or JAVA_VERSION not in probe_text:
        raise ValueError("staged Java probe does not match the pinned runtime")
    runtime_files = [
        entry
        for entry in _tree_receipts(artifact_root, "runtime")
        if entry["path"] not in {JAVA_EXECUTABLE, JAVA_PROBE}
    ]
    modules = _tree_receipts(artifact_root, MODULE_ROOT)
    configuration = _file_receipt(
        artifact_root / CONFIGURATION,
        CONFIGURATION,
    )
    settings = _typed_properties((artifact_root / CONFIGURATION).read_bytes())
    population = binding["required_source_patients"]
    return {
        "schema_version": GENERATION_SPEC_VERSION,
        "generator": {
            "repository": "synthetichealth/synthea",
            "release_tag": SYNTHEA_RELEASE,
            "commit": SYNTHEA_COMMIT,
            "jar": jar,
        },
        "java_runtime": {
            "vendor": JAVA_VENDOR,
            "version": JAVA_VERSION,
            "executable": executable,
            "distribution_files": runtime_files,
            "version_probe": probe,
            "version_probe_argv": [
                JAVA_EXECUTABLE,
                "-version",
            ],
        },
        "invocation": {
            "argv": [
                JAVA_EXECUTABLE,
                "-jar",
                "generator/synthea.jar",
                "-s",
                str(GENERATION_SEED),
                "-cs",
                str(GENERATION_SEED),
                "-p",
                str(population),
                "-r",
                REFERENCE_DATE_COMPACT,
                "-e",
                REFERENCE_DATE_COMPACT,
                "-c",
                CONFIGURATION,
                "-d",
                MODULE_ROOT,
            ],
            "environment": dict(PROCESS_ENVIRONMENT),
            "seed": GENERATION_SEED,
            "population": population,
            "reference_date": REFERENCE_DATE,
            "locale": "en-US",
            "timezone": "UTC",
        },
        "configuration_files": [configuration],
        "module_files": modules,
        "exporter_settings": settings,
        "output": {
            "root": OUTPUT_ROOT,
            "allowed_suffixes": [".json"],
            "required_patient_count": population,
            "max_entries": population,
            "max_file_bytes": 64 * 1024 * 1024,
            "max_total_bytes": 4 * 1024 * 1024 * 1024,
        },
        "power_gate": binding,
        "model_calls": 0,
    }


def normalized_jdk_member(member: tarfile.TarInfo) -> Path:
    """Return the safe path below runtime/ for one exact Temurin archive member."""

    path = PurePosixPath(member.name)
    if path.parts == (JDK_RELEASE,) and member.isdir():
        return Path()
    if (
        path.is_absolute()
        or len(path.parts) < 2
        or path.parts[0] != JDK_RELEASE
        or any(part in {"", ".", ".."} for part in path.parts)
        or member.issym()
        or member.islnk()
        or not (member.isfile() or member.isdir())
    ):
        raise ValueError(f"unsafe JDK archive member: {member.name}")
    return Path(*path.parts[1:])


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = __import__("hashlib").sha256()
    received = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            received += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), received


def _verify_upstream(
    synthea_jar: Path,
    jdk_archive: Path,
    synthea_checkout: Path,
) -> None:
    jar_hash, jar_bytes = _sha256_file(synthea_jar)
    if (jar_hash, jar_bytes) != (SYNTHEA_JAR_SHA256, SYNTHEA_JAR_BYTES):
        raise ValueError("Synthea JAR does not match the official v4.0.0 asset")
    jdk_hash, _ = _sha256_file(jdk_archive)
    if jdk_hash != JDK_ARCHIVE_SHA256:
        raise ValueError("JDK archive does not match the pinned Temurin asset")
    head = subprocess.run(
        ["git", "-C", str(synthea_checkout), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    tag = subprocess.run(
        ["git", "-C", str(synthea_checkout), "rev-parse", f"{SYNTHEA_RELEASE}^{{commit}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    module_relative = "src/main/resources/modules"
    status = subprocess.run(
        [
            "git",
            "-C",
            str(synthea_checkout),
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            module_relative,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if head != SYNTHEA_COMMIT or tag != SYNTHEA_COMMIT or status:
        raise ValueError("Synthea module checkout is not the clean pinned release commit")


def _extract_jdk(archive: Path, destination: Path) -> None:
    expanded = 0
    entries = 0
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle:
            entries += 1
            if entries > MAX_JDK_ARCHIVE_ENTRIES:
                raise ValueError("JDK archive exceeds entry bound")
            relative = normalized_jdk_member(member)
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(member.mode & 0o755)
                continue
            expanded += member.size
            if expanded > MAX_JDK_EXPANDED_BYTES:
                raise ValueError("JDK archive exceeds expanded byte bound")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = bundle.extractfile(member)
            if source is None:
                raise ValueError(f"JDK archive member cannot be read: {member.name}")
            with target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            target.chmod(member.mode & 0o755)


def _copy_tree(source: Path, destination: Path) -> None:
    for directory, directory_names, file_names in os.walk(source, followlinks=False):
        directory_path = Path(directory)
        relative = directory_path.relative_to(source)
        target_directory = destination / relative
        target_directory.mkdir(parents=True, exist_ok=True)
        for name in directory_names:
            child = directory_path / name
            metadata = child.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ValueError(f"module source contains unsafe directory: {child}")
        for name in file_names:
            child = directory_path / name
            metadata = child.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"module source contains unsafe file: {child}")
            shutil.copyfile(child, target_directory / name)


def stage_generation(
    *,
    synthea_jar: Path,
    jdk_archive: Path,
    synthea_checkout: Path,
    artifact_root: Path,
    power_spec: object,
    power_receipt: object,
) -> dict[str, Any]:
    """Create one clean, exact-input artifact root and prospective spec."""

    synthea_jar = Path(synthea_jar)
    jdk_archive = Path(jdk_archive)
    synthea_checkout = Path(synthea_checkout)
    artifact_root = Path(artifact_root)
    if artifact_root.exists():
        raise ValueError("artifact root already exists")
    artifact_root.parent.mkdir(parents=True, exist_ok=True)
    _verify_upstream(synthea_jar, jdk_archive, synthea_checkout)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{artifact_root.name}.staging-",
            dir=artifact_root.parent,
        )
    )
    try:
        (staging / "generator").mkdir()
        shutil.copyfile(synthea_jar, staging / "generator" / "synthea.jar")
        _extract_jdk(jdk_archive, staging / "runtime")
        module_source = synthea_checkout / "src" / "main" / "resources" / "modules"
        _copy_tree(module_source, staging / MODULE_ROOT)
        (staging / "configuration").mkdir()
        shutil.copyfile(
            Path(__file__).resolve().parent / "fixtures" / "a11b_synthea.properties",
            staging / CONFIGURATION,
        )
        probe = subprocess.run(
            [
                str(staging / JAVA_EXECUTABLE),
                "-version",
            ],
            cwd=staging,
            env=dict(PROCESS_ENVIRONMENT),
            capture_output=True,
            check=False,
            timeout=60,
        )
        probe_data = probe.stdout + probe.stderr
        if probe.returncode != 0:
            raise ValueError("pinned Java runtime probe failed")
        (staging / JAVA_PROBE).write_bytes(probe_data)
        staging.rename(artifact_root)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return build_generation_spec(
        artifact_root,
        power_spec=power_spec,
        power_receipt=power_receipt,
    )


def _freeze_tree(root: Path) -> None:
    directories: list[Path] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        directories.append(directory_path)
        for name in directory_names:
            child = directory_path / name
            if child.is_symlink():
                raise ValueError("cannot freeze a tree containing symlinks")
        for name in file_names:
            child = directory_path / name
            child.chmod(0o500 if child == root / JAVA_EXECUTABLE else 0o400)
    for directory in reversed(directories):
        directory.chmod(0o500)


def run_generation(
    spec: object,
    *,
    artifact_root: Path,
    power_spec: object,
    power_receipt: object,
) -> tuple[dict[str, Any], bytes]:
    """Run one exact Synthea process and compile its deterministic receipt."""

    artifact_root = Path(artifact_root)
    if (artifact_root / OUTPUT_ROOT).exists():
        raise ValueError("generation output already exists")
    expected_spec = build_generation_spec(
        artifact_root,
        power_spec=power_spec,
        power_receipt=power_receipt,
    )
    if canonical_bytes(spec) != canonical_bytes(expected_spec):
        raise ValueError("generation spec does not match the staged exact inputs")
    assert isinstance(spec, dict)
    process = subprocess.run(
        spec["invocation"]["argv"],
        cwd=artifact_root,
        env=spec["invocation"]["environment"],
        capture_output=True,
        check=False,
    )
    log = process.stdout + process.stderr
    if process.returncode != 0:
        raise RuntimeError(
            f"Synthea generation failed with exit status {process.returncode}"
        )
    receipt = compile_generation_receipt(
        spec,
        artifact_root=artifact_root,
        power_spec=power_spec,
        power_receipt=power_receipt,
    )
    _freeze_tree(artifact_root)
    return receipt, log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    stage = subparsers.add_parser("stage")
    stage.add_argument("--synthea-jar", type=Path, required=True)
    stage.add_argument("--jdk-archive", type=Path, required=True)
    stage.add_argument("--synthea-checkout", type=Path, required=True)
    stage.add_argument("--artifact-root", type=Path, required=True)
    stage.add_argument("--spec-output", type=Path, required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--artifact-root", type=Path, required=True)
    run.add_argument("--spec", type=Path, required=True)
    run.add_argument("--receipt-output", type=Path, required=True)
    run.add_argument("--log-output", type=Path, required=True)
    for child in (stage, run):
        child.add_argument("--power-spec", type=Path, required=True)
        child.add_argument("--power-receipt", type=Path, required=True)
    args = parser.parse_args()
    power_spec = _load_json(args.power_spec)
    power_receipt = _load_json(args.power_receipt)
    if args.command == "stage":
        spec = stage_generation(
            synthea_jar=args.synthea_jar,
            jdk_archive=args.jdk_archive,
            synthea_checkout=args.synthea_checkout,
            artifact_root=args.artifact_root,
            power_spec=power_spec,
            power_receipt=power_receipt,
        )
        _write_new_file(
            args.spec_output,
            json.dumps(spec, sort_keys=True, indent=2).encode("utf-8") + b"\n",
        )
        return
    spec = _load_json(args.spec)
    receipt, log = run_generation(
        spec,
        artifact_root=args.artifact_root,
        power_spec=power_spec,
        power_receipt=power_receipt,
    )
    _write_new_file(args.log_output, log)
    _write_new_file(
        args.receipt_output,
        json.dumps(receipt, sort_keys=True, indent=2).encode("utf-8") + b"\n",
    )


if __name__ == "__main__":
    main()
