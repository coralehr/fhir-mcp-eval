#!/usr/bin/env python3
"""Compile and verify a sealed, zero-model Synthea generation receipt for A11b.

The public compiler accepts only a prospective generation spec, its bound power
gate, and one artifact root. It never invokes Synthea or a model. Instead, it
binds the exact registered generator/runtime/configuration inputs and the
complete candidate output tree to the receipt it emits.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from a11_evidence_core import canonical_bytes, sha256
from a11b_power_gate import spec_sha256 as power_spec_sha256
from a11b_power_gate import verify_power_receipt


GENERATION_SPEC_VERSION = "a11b-synthea-generation-spec-v1"
GENERATION_RECEIPT_VERSION = "a11b-synthea-generation-receipt-v1"
_SPEC_FIELDS = {
    "schema_version",
    "generator",
    "java_runtime",
    "invocation",
    "configuration_files",
    "module_files",
    "exporter_settings",
    "output",
    "power_gate",
    "model_calls",
}
_GENERATOR_FIELDS = {"repository", "release_tag", "commit", "jar"}
_JAVA_FIELDS = {
    "vendor",
    "version",
    "executable",
    "distribution_files",
    "version_probe",
    "version_probe_argv",
}
_INVOCATION_FIELDS = {
    "argv",
    "environment",
    "seed",
    "population",
    "reference_date",
    "locale",
    "timezone",
}
_OUTPUT_FIELDS = {
    "root",
    "allowed_suffixes",
    "required_patient_count",
    "max_entries",
    "max_file_bytes",
    "max_total_bytes",
}
_POWER_FIELDS = {
    "spec_sha256",
    "receipt_sha256",
    "required_source_patients",
}
_FILE_FIELDS = {"path", "sha256", "bytes"}
_DEPENDENCY_FILES = (
    "a11_evidence_core.py",
    "a11b_generation_receipt.py",
    "a11b_power_gate.py",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
MAX_REGISTERED_INPUTS = 20_000
MAX_REGISTERED_INPUT_FILE_BYTES = 512 * 1024 * 1024
MAX_REGISTERED_INPUT_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_OUTPUT_ENTRIES = 100_000
MAX_OUTPUT_FILE_BYTES = 512 * 1024 * 1024
MAX_OUTPUT_TOTAL_BYTES = 4 * 1024 * 1024 * 1024


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _load_json_bytes(data: bytes, location: str) -> Any:
    try:
        return json.loads(
            data,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {location}") from exc


def _require_finite_json(value: Any, name: str) -> None:
    if value is None or isinstance(value, (bool, str)) or type(value) is int:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{name} must contain only finite JSON values")
        return
    if isinstance(value, list):
        for item in value:
            _require_finite_json(item, name)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _require_finite_json(item, name)
        return
    raise ValueError(f"{name} must contain only finite JSON values")


def _safe_relative(value: object, name: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError(f"{name} is unsafe")
    return path


def _file_spec(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _FILE_FIELDS:
        raise ValueError(f"{name} fields are invalid")
    path = _safe_relative(value.get("path"), f"{name} path").as_posix()
    digest = value.get("sha256")
    size = value.get("bytes")
    if _SHA256.fullmatch(str(digest or "")) is None:
        raise ValueError(f"{name} sha256 is invalid")
    if type(size) is not int or size < 0:
        raise ValueError(f"{name} byte count is invalid")
    return {"path": path, "sha256": digest, "bytes": size}


def _file_specs(value: object, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty list")
    entries = [_file_spec(item, f"{name} entry") for item in value]
    paths = [entry["path"] for entry in entries]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError(f"{name} must be uniquely sorted by path")
    return entries


def _strings(value: object, name: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item or "\x00" in item for item in value)
    ):
        raise ValueError(f"{name} must be a non-empty string list")
    return list(value)


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _validate_spec(
    spec: object, power_spec: object, power_receipt: object
) -> dict[str, Any]:
    _require_finite_json(spec, "generation spec")
    if not isinstance(spec, dict) or set(spec) != _SPEC_FIELDS:
        raise ValueError("generation spec fields are invalid")
    if spec.get("schema_version") != GENERATION_SPEC_VERSION:
        raise ValueError("generation spec version is invalid")
    if spec.get("model_calls") != 0:
        raise ValueError("generation must have zero model calls")

    verify_power_receipt(power_spec, power_receipt)
    if not isinstance(power_receipt, dict):
        raise ValueError("power receipt is invalid")
    power = spec.get("power_gate")
    if not isinstance(power, dict) or set(power) != _POWER_FIELDS:
        raise ValueError("power gate fields are invalid")
    required_source = power_receipt.get("required_source_patients")
    if (
        power.get("spec_sha256") != power_spec_sha256(power_spec)
        or power.get("receipt_sha256") != sha256(canonical_bytes(power_receipt))
        or type(required_source) is not int
        or power.get("required_source_patients") != required_source
    ):
        raise ValueError("generation spec is not bound to the verified power gate")

    generator = spec.get("generator")
    if not isinstance(generator, dict) or set(generator) != _GENERATOR_FIELDS:
        raise ValueError("generator fields are invalid")
    if generator.get("repository") != "synthetichealth/synthea":
        raise ValueError("generator repository is invalid")
    if not isinstance(generator.get("release_tag"), str) or not generator["release_tag"]:
        raise ValueError("generator release tag is invalid")
    if _GIT_COMMIT.fullmatch(str(generator.get("commit") or "")) is None:
        raise ValueError("generator commit is invalid")
    jar = _file_spec(generator.get("jar"), "generator jar")

    runtime = spec.get("java_runtime")
    if not isinstance(runtime, dict) or set(runtime) != _JAVA_FIELDS:
        raise ValueError("Java runtime fields are invalid")
    for field in ("vendor", "version"):
        if not isinstance(runtime.get(field), str) or not runtime[field].strip():
            raise ValueError(f"Java runtime {field} is invalid")
    executable = _file_spec(runtime.get("executable"), "Java executable")
    runtime_files = _file_specs(
        runtime.get("distribution_files"), "Java distribution files"
    )
    for entry in runtime_files:
        if not entry["path"].startswith("runtime/"):
            raise ValueError("Java distribution file is outside runtime/")
    probe = _file_spec(runtime.get("version_probe"), "Java version probe")
    probe_argv = _strings(runtime.get("version_probe_argv"), "Java version probe argv")
    if probe_argv != [
        executable["path"],
        "-XshowSettings:properties",
        "-version",
    ]:
        raise ValueError("Java version probe argv is invalid")

    invocation = spec.get("invocation")
    if not isinstance(invocation, dict) or set(invocation) != _INVOCATION_FIELDS:
        raise ValueError("generation invocation fields are invalid")
    argv = _strings(invocation.get("argv"), "generation argv")
    if len(argv) < 9 or argv[:3] != [executable["path"], "-jar", jar["path"]]:
        raise ValueError("generation argv does not bind the registered runtime and JAR")
    seed = invocation.get("seed")
    population = invocation.get("population")
    if type(seed) is not int or type(population) is not int or population <= 0:
        raise ValueError("generation seed or population is invalid")
    reference_date = invocation.get("reference_date")
    if not isinstance(reference_date, str):
        raise ValueError("reference date is invalid")
    try:
        parsed_reference_date = dt.date.fromisoformat(reference_date)
    except ValueError as exc:
        raise ValueError("reference date is invalid") from exc
    expected_flags = {
        "-s": str(seed),
        "-p": str(population),
        "-r": parsed_reference_date.strftime("%Y%m%d"),
    }
    for flag, expected in expected_flags.items():
        if argv.count(flag) != 1:
            raise ValueError(f"generation argv must contain exactly one {flag}")
        index = argv.index(flag)
        if index + 1 >= len(argv) or argv[index + 1] != expected:
            raise ValueError(f"generation argv {flag} value is invalid")
    environment = invocation.get("environment")
    if not isinstance(environment, dict) or set(environment) != {"LANG", "LC_ALL", "TZ"}:
        raise ValueError("generation environment fields are invalid")
    if any(not isinstance(value, str) or not value for value in environment.values()):
        raise ValueError("generation environment values are invalid")
    if environment["LANG"] != environment["LC_ALL"]:
        raise ValueError("generation locale environment is inconsistent")
    locale = invocation.get("locale")
    timezone = invocation.get("timezone")
    if (
        not isinstance(locale, str)
        or not locale
        or timezone != "UTC"
        or environment["TZ"] != timezone
    ):
        raise ValueError("generation locale or timezone is invalid")
    if population != required_source:
        raise ValueError("generation population does not match the power gate")

    configuration = _file_specs(spec.get("configuration_files"), "configuration files")
    modules = _file_specs(spec.get("module_files"), "module files")
    for entry in configuration:
        if not entry["path"].startswith("configuration/"):
            raise ValueError("configuration file is outside configuration/")
    for entry in modules:
        if not entry["path"].startswith("modules/"):
            raise ValueError("module file is outside modules/")

    exporter = spec.get("exporter_settings")
    if not isinstance(exporter, dict) or not exporter:
        raise ValueError("exporter settings are invalid")
    if exporter.get("exporter.fhir.export") is not True:
        raise ValueError("FHIR export must be enabled")

    output = spec.get("output")
    if not isinstance(output, dict) or set(output) != _OUTPUT_FIELDS:
        raise ValueError("output fields are invalid")
    output_root = _safe_relative(output.get("root"), "output root").as_posix()
    if exporter.get("exporter.baseDirectory") != output_root:
        raise ValueError("exporter output directory is inconsistent")
    suffixes = _strings(output.get("allowed_suffixes"), "allowed output suffixes")
    if suffixes != [".json"]:
        raise ValueError("allowed output suffixes must be exactly .json")
    required_patients = _positive_int(
        output.get("required_patient_count"), "required patient count"
    )
    if required_patients != population:
        raise ValueError("required patient count does not match generation population")
    bounds = {
        key: _positive_int(output.get(key), key.replace("_", " "))
        for key in ("max_entries", "max_file_bytes", "max_total_bytes")
    }

    input_entries = [
        jar,
        executable,
        probe,
        *runtime_files,
        *configuration,
        *modules,
    ]
    input_paths = [entry["path"] for entry in input_entries]
    if len(input_paths) != len(set(input_paths)):
        raise ValueError("registered input paths overlap")
    if any(path == output_root or path.startswith(f"{output_root}/") for path in input_paths):
        raise ValueError("registered input overlaps output root")
    if (
        len(input_entries) > MAX_REGISTERED_INPUTS
        or any(
            entry["bytes"] > MAX_REGISTERED_INPUT_FILE_BYTES
            for entry in input_entries
        )
        or sum(entry["bytes"] for entry in input_entries)
        > MAX_REGISTERED_INPUT_TOTAL_BYTES
    ):
        raise ValueError("registered inputs exceed hard safety bounds")
    if (
        bounds["max_entries"] > MAX_OUTPUT_ENTRIES
        or bounds["max_file_bytes"] > MAX_OUTPUT_FILE_BYTES
        or bounds["max_total_bytes"] > MAX_OUTPUT_TOTAL_BYTES
    ):
        raise ValueError("generated output bounds exceed hard safety limits")
    return {
        "generator": copy.deepcopy(generator),
        "runtime": copy.deepcopy(runtime),
        "invocation": copy.deepcopy(invocation),
        "configuration": configuration,
        "modules": modules,
        "exporter": copy.deepcopy(exporter),
        "output_root": output_root,
        "suffixes": suffixes,
        "required_patients": required_patients,
        "bounds": bounds,
        "input_entries": input_entries,
        "power": copy.deepcopy(power),
    }


def _inventory(root: Path) -> list[str]:
    try:
        root_metadata = root.lstat()
    except FileNotFoundError as exc:
        raise ValueError("artifact root does not exist") from exc
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise ValueError("artifact root must be a real directory")
    paths: list[str] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in sorted(directory_names):
            child = directory_path / name
            metadata = child.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ValueError(f"artifact tree contains unsafe directory: {name}")
        for name in sorted(file_names):
            child = directory_path / name
            metadata = child.lstat()
            relative = child.relative_to(root).as_posix()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"artifact tree contains unsafe file: {relative}")
            if metadata.st_nlink != 1:
                raise ValueError(f"artifact tree contains hard-linked file: {relative}")
            _safe_relative(relative, "artifact path")
            paths.append(relative)
    return sorted(paths)


def _read_registered(
    root_fd: int,
    relative: str,
    max_bytes: int,
    *,
    require_executable: bool = False,
) -> bytes:
    path = _safe_relative(relative, "artifact path")
    directory_fd = os.dup(root_fd)
    try:
        for part in path.parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            path.parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        try:
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError(f"artifact is not a unique regular file: {relative}")
            if require_executable and not metadata.st_mode & 0o111:
                raise ValueError(f"registered Java runtime is not executable: {relative}")
            if metadata.st_size > max_bytes:
                raise ValueError(f"artifact exceeds byte bound: {relative}")
            chunks: list[bytes] = []
            received = 0
            while True:
                chunk = os.read(file_fd, min(1024 * 1024, max_bytes + 1 - received))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received > max_bytes:
                    raise ValueError(f"artifact exceeds byte bound: {relative}")
            if received != metadata.st_size:
                raise ValueError(f"artifact byte count changed: {relative}")
            return b"".join(chunks)
        finally:
            os.close(file_fd)
    except OSError as exc:
        raise ValueError(f"unsafe artifact path: {relative}") from exc
    finally:
        os.close(directory_fd)


def _iter_resources(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for child in value:
            yield from _iter_resources(child)
        return
    if not isinstance(value, dict):
        return
    if value.get("resourceType") == "Bundle":
        entries = value.get("entry")
        if not isinstance(entries, list):
            raise ValueError("FHIR Bundle entry must be a list")
        for entry in entries:
            if not isinstance(entry, dict) or "resource" not in entry:
                raise ValueError("FHIR Bundle entry is invalid")
            yield from _iter_resources(entry["resource"])
        return
    if isinstance(value.get("resourceType"), str):
        yield value


def _dependency_receipts() -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parent
    receipts = []
    for relative in sorted(_DEPENDENCY_FILES):
        payload = (root / relative).read_bytes()
        receipts.append(
            {"path": relative, "sha256": sha256(payload), "bytes": len(payload)}
        )
    return receipts


def compile_generation_receipt(
    spec: object,
    *,
    artifact_root: Path,
    power_spec: object,
    power_receipt: object,
) -> dict[str, Any]:
    """Compile the complete generation receipt without running Synthea or a model."""

    validated = _validate_spec(spec, power_spec, power_receipt)
    dependencies_before = _dependency_receipts()
    artifact_root = Path(artifact_root)
    inventory = _inventory(artifact_root)
    input_by_path = {entry["path"]: entry for entry in validated["input_entries"]}
    output_prefix = f'{validated["output_root"]}/'
    output_paths = [path for path in inventory if path.startswith(output_prefix)]
    if not output_paths:
        raise ValueError("generated output tree is empty")
    unexpected = sorted(set(inventory) - set(input_by_path) - set(output_paths))
    missing = sorted(set(input_by_path) - set(inventory))
    if unexpected or missing:
        raise ValueError(
            "artifact tree differs from registered inputs: "
            f"missing={missing}, unexpected={unexpected}"
        )
    if len(output_paths) > validated["bounds"]["max_entries"]:
        raise ValueError("generated output exceeds entry bound")

    input_entries: list[dict[str, Any]] = []
    output_entries: list[dict[str, Any]] = []
    patient_receipts: dict[str, dict[str, str]] = {}
    total_output_bytes = 0
    content_digest = hashlib.sha256()
    probe_data: bytes | None = None
    root_fd = os.open(artifact_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for path, expected in sorted(input_by_path.items()):
            data = _read_registered(
                root_fd,
                path,
                max(expected["bytes"], 1),
                require_executable=(
                    path == validated["runtime"]["executable"]["path"]
                ),
            )
            actual = {"path": path, "sha256": sha256(data), "bytes": len(data)}
            if actual != expected:
                raise ValueError(f"registered input changed: {path}")
            input_entries.append(actual)
            if path == validated["runtime"]["version_probe"]["path"]:
                probe_data = data

        if probe_data is None:
            raise ValueError("registered Java version probe is missing")
        try:
            probe_text = probe_data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Java version probe is not UTF-8") from exc
        if (
            validated["runtime"]["vendor"] not in probe_text
            or validated["runtime"]["version"] not in probe_text
        ):
            raise ValueError("Java version probe does not contain the registered runtime")

        for path in output_paths:
            suffix = PurePosixPath(path).suffix.lower()
            if suffix not in validated["suffixes"]:
                raise ValueError(f"generated output suffix is not allowed: {path}")
            data = _read_registered(
                root_fd, path, validated["bounds"]["max_file_bytes"]
            )
            total_output_bytes += len(data)
            if total_output_bytes > validated["bounds"]["max_total_bytes"]:
                raise ValueError("generated output exceeds total byte bound")
            entry = {"path": path, "sha256": sha256(data), "bytes": len(data)}
            output_entries.append(entry)
            encoded_path = path.encode("utf-8")
            content_digest.update(len(encoded_path).to_bytes(8, "big"))
            content_digest.update(encoded_path)
            content_digest.update(len(data).to_bytes(8, "big"))
            content_digest.update(data)
            value = _load_json_bytes(data, path)
            resource_count = 0
            for resource in _iter_resources(value):
                resource_count += 1
                if resource.get("resourceType") != "Patient":
                    continue
                patient_id = resource.get("id")
                if not isinstance(patient_id, str) or not patient_id:
                    raise ValueError(f"generated Patient has no id: {path}")
                if patient_id in patient_receipts:
                    raise ValueError(f"duplicate generated Patient id: {patient_id}")
                patient_receipts[patient_id] = {
                    "id_sha256": sha256(patient_id.encode("utf-8")),
                    "resource_sha256": sha256(canonical_bytes(resource)),
                }
            if resource_count == 0:
                raise ValueError("generated JSON contains no FHIR resource")
    finally:
        os.close(root_fd)

    if len(patient_receipts) != validated["required_patients"]:
        raise ValueError(
            "generated Patient count does not match the power-gated population"
        )
    patient_manifest = [patient_receipts[key] for key in sorted(patient_receipts)]
    public_output_entries = [
        {
            "path_sha256": sha256(entry["path"].encode("utf-8")),
            "sha256": entry["sha256"],
            "bytes": entry["bytes"],
        }
        for entry in output_entries
    ]
    dependencies_after = _dependency_receipts()
    if dependencies_after != dependencies_before:
        raise ValueError("generation receipt dependencies changed during compilation")
    assert isinstance(spec, dict)
    return {
        "schema_version": GENERATION_RECEIPT_VERSION,
        "generation_spec_sha256": sha256(canonical_bytes(spec)),
        "power_gate": validated["power"],
        "generator": {
            "repository": validated["generator"]["repository"],
            "release_tag": validated["generator"]["release_tag"],
            "commit": validated["generator"]["commit"],
            "jar": validated["generator"]["jar"],
        },
        "java_runtime": validated["runtime"],
        "invocation": validated["invocation"],
        "exporter_settings": validated["exporter"],
        "registered_inputs": {
            "entries": input_entries,
            "entry_manifest_sha256": sha256(canonical_bytes(input_entries)),
        },
        "raw_output": {
            "entries": public_output_entries,
            "entry_manifest_sha256": sha256(canonical_bytes(public_output_entries)),
            "content_sha256": content_digest.hexdigest(),
            "bytes": total_output_bytes,
        },
        "source_population": {
            "patients": len(patient_receipts),
            "patient_manifest_sha256": sha256(canonical_bytes(patient_manifest)),
            "patient_identifiers_disclosed": False,
        },
        "compiler_dependencies": dependencies_before,
        "efficacy_artifacts_opened": False,
        "gold_artifacts_opened": False,
        "model_calls": 0,
    }


def verify_generation_receipt(
    spec: object,
    receipt: object,
    *,
    artifact_root: Path,
    power_spec: object,
    power_receipt: object,
) -> None:
    expected = compile_generation_receipt(
        spec,
        artifact_root=artifact_root,
        power_spec=power_spec,
        power_receipt=power_receipt,
    )
    if not isinstance(receipt, dict) or canonical_bytes(receipt) != canonical_bytes(expected):
        raise ValueError("generation receipt does not match the sealed artifacts")


def _load_json(path: Path) -> object:
    return _load_json_bytes(path.read_bytes(), path.name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("compile", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("--spec", type=Path, required=True)
        child.add_argument("--power-spec", type=Path, required=True)
        child.add_argument("--power-receipt", type=Path, required=True)
        child.add_argument("--artifact-root", type=Path, required=True)
        if command == "compile":
            child.add_argument("--output", type=Path, required=True)
        else:
            child.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    spec = _load_json(args.spec)
    power_spec = _load_json(args.power_spec)
    power_receipt = _load_json(args.power_receipt)
    if args.command == "compile":
        receipt = compile_generation_receipt(
            spec,
            artifact_root=args.artifact_root,
            power_spec=power_spec,
            power_receipt=power_receipt,
        )
        args.output.write_bytes(
            json.dumps(receipt, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        )
    else:
        verify_generation_receipt(
            spec,
            _load_json(args.receipt),
            artifact_root=args.artifact_root,
            power_spec=power_spec,
            power_receipt=power_receipt,
        )


if __name__ == "__main__":
    main()
