#!/usr/bin/env python3
"""Stage and run the fresh, zero-model A11b successor source generation."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import a11b_synthea_runner as historical


SYNTHEA_RELEASE = historical.SYNTHEA_RELEASE
SYNTHEA_COMMIT = historical.SYNTHEA_COMMIT
JAVA_VENDOR = historical.JAVA_VENDOR
JAVA_VERSION = historical.JAVA_VERSION
JAVA_EXECUTABLE = historical.JAVA_EXECUTABLE
JAVA_PROBE = historical.JAVA_PROBE
CONFIGURATION = historical.CONFIGURATION
MODULE_ROOT = historical.MODULE_ROOT
OUTPUT_ROOT = historical.OUTPUT_ROOT
PROCESS_ENVIRONMENT = historical.PROCESS_ENVIRONMENT

GENERATION_SEED = 20_260_718
REFERENCE_DATE = "2026-07-17"
REFERENCE_DATE_COMPACT = "20260717"
MAX_OUTPUT_FILE_BYTES = 128 * 1024 * 1024
GENERATION_TIMEOUT_SECONDS = 3_600


def build_generation_spec(
    artifact_root: Path,
    *,
    power_spec: object,
    power_receipt: object,
) -> dict[str, Any]:
    """Build the new prospective spec while reusing only verified primitives."""

    artifact_root = Path(artifact_root)
    binding = historical._power_binding(power_spec, power_receipt)
    jar = historical._file_receipt(
        artifact_root / "generator" / "synthea.jar",
        "generator/synthea.jar",
    )
    executable = historical._file_receipt(
        artifact_root / JAVA_EXECUTABLE,
        JAVA_EXECUTABLE,
    )
    if not (artifact_root / JAVA_EXECUTABLE).stat().st_mode & 0o111:
        raise ValueError("staged Java executable is not executable")
    probe = historical._file_receipt(artifact_root / JAVA_PROBE, JAVA_PROBE)
    probe_text = (artifact_root / JAVA_PROBE).read_text(encoding="utf-8")
    if JAVA_VENDOR not in probe_text or JAVA_VERSION not in probe_text:
        raise ValueError("staged Java probe does not match the pinned runtime")
    runtime_files = [
        entry
        for entry in historical._tree_receipts(artifact_root, "runtime")
        if entry["path"] not in {JAVA_EXECUTABLE, JAVA_PROBE}
    ]
    modules = historical._tree_receipts(artifact_root, MODULE_ROOT)
    configuration = historical._file_receipt(
        artifact_root / CONFIGURATION,
        CONFIGURATION,
    )
    settings = historical._typed_properties(
        (artifact_root / CONFIGURATION).read_bytes()
    )
    population = binding["required_source_patients"]
    return {
        "schema_version": historical.GENERATION_SPEC_VERSION,
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
            "version_probe_argv": [JAVA_EXECUTABLE, "-version"],
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
            "max_file_bytes": MAX_OUTPUT_FILE_BYTES,
            "max_total_bytes": 4 * 1024 * 1024 * 1024,
        },
        "power_gate": binding,
        "model_calls": 0,
    }


def stage_generation(
    *,
    synthea_jar: Path,
    jdk_archive: Path,
    synthea_checkout: Path,
    artifact_root: Path,
    power_spec: object,
    power_receipt: object,
) -> dict[str, Any]:
    """Create one clean successor input root and its prospective spec."""

    synthea_jar = Path(synthea_jar)
    jdk_archive = Path(jdk_archive)
    synthea_checkout = Path(synthea_checkout)
    artifact_root = Path(artifact_root)
    if artifact_root.exists():
        raise ValueError("artifact root already exists")
    artifact_root.parent.mkdir(parents=True, exist_ok=True)
    historical._verify_upstream(synthea_jar, jdk_archive, synthea_checkout)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{artifact_root.name}.staging-",
            dir=artifact_root.parent,
        )
    )
    try:
        (staging / "generator").mkdir()
        shutil.copyfile(synthea_jar, staging / "generator/synthea.jar")
        historical._extract_jdk(jdk_archive, staging / "runtime")
        module_source = synthea_checkout / "src/main/resources/modules"
        historical._copy_tree(module_source, staging / MODULE_ROOT)
        (staging / "configuration").mkdir()
        shutil.copyfile(
            Path(__file__).resolve().parent / "fixtures/a11b_synthea.properties",
            staging / CONFIGURATION,
        )
        probe = historical.subprocess.run(
            [str(staging / JAVA_EXECUTABLE), "-version"],
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
        spec = build_generation_spec(
            staging,
            power_spec=power_spec,
            power_receipt=power_receipt,
        )
        staging.rename(artifact_root)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return spec


def run_generation(
    spec: object,
    *,
    artifact_root: Path,
    power_spec: object,
    power_receipt: object,
) -> tuple[dict[str, Any], bytes]:
    """Run exactly one fresh Synthea process and freeze its receipt tree."""

    artifact_root = Path(artifact_root)
    if (artifact_root / OUTPUT_ROOT).exists():
        raise ValueError("generation output already exists")
    expected_spec = build_generation_spec(
        artifact_root,
        power_spec=power_spec,
        power_receipt=power_receipt,
    )
    if historical.canonical_bytes(spec) != historical.canonical_bytes(expected_spec):
        raise ValueError("generation spec does not match the successor inputs")
    assert isinstance(spec, dict)
    process = historical.subprocess.run(
        spec["invocation"]["argv"],
        cwd=artifact_root,
        env=spec["invocation"]["environment"],
        capture_output=True,
        check=False,
        timeout=GENERATION_TIMEOUT_SECONDS,
    )
    log = process.stdout + process.stderr
    if process.returncode != 0:
        raise RuntimeError(
            f"Synthea generation failed with exit status {process.returncode}"
        )
    receipt = historical.compile_generation_receipt(
        spec,
        artifact_root=artifact_root,
        power_spec=power_spec,
        power_receipt=power_receipt,
    )
    historical._freeze_tree(artifact_root)
    return receipt, log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    children = parser.add_subparsers(dest="command", required=True)
    stage = children.add_parser("stage")
    stage.add_argument("--synthea-jar", type=Path, required=True)
    stage.add_argument("--jdk-archive", type=Path, required=True)
    stage.add_argument("--synthea-checkout", type=Path, required=True)
    stage.add_argument("--artifact-root", type=Path, required=True)
    stage.add_argument("--spec-output", type=Path, required=True)
    run = children.add_parser("run")
    run.add_argument("--artifact-root", type=Path, required=True)
    run.add_argument("--spec", type=Path, required=True)
    run.add_argument("--receipt-output", type=Path, required=True)
    run.add_argument("--log-output", type=Path, required=True)
    for child in (stage, run):
        child.add_argument("--power-spec", type=Path, required=True)
        child.add_argument("--power-receipt", type=Path, required=True)
    args = parser.parse_args()
    power_spec = historical._load_json(args.power_spec)
    power_receipt = historical._load_json(args.power_receipt)
    if args.command == "stage":
        spec = stage_generation(
            synthea_jar=args.synthea_jar,
            jdk_archive=args.jdk_archive,
            synthea_checkout=args.synthea_checkout,
            artifact_root=args.artifact_root,
            power_spec=power_spec,
            power_receipt=power_receipt,
        )
        historical._write_new_file(
            args.spec_output,
            json.dumps(spec, sort_keys=True, indent=2).encode("utf-8") + b"\n",
        )
        return
    spec = historical._load_json(args.spec)
    receipt, log = run_generation(
        spec,
        artifact_root=args.artifact_root,
        power_spec=power_spec,
        power_receipt=power_receipt,
    )
    historical._write_new_file(args.log_output, log)
    historical._write_new_file(
        args.receipt_output,
        json.dumps(receipt, sort_keys=True, indent=2).encode("utf-8") + b"\n",
    )


if __name__ == "__main__":
    main()
