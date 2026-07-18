#!/usr/bin/env python3
"""Seal the A11b answer schedule and trusted-executor service bundle."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any

import a11_answer_harness
import a11b_grading
import experiment_anchor
import experiment_executor
import experiment_executor_install as install
import experiment_executor_service as service
import experiment_witness as witness
import trusted_codex_driver


CONTROLLER_VERSION = "a11-controller-v4"
EXPERIMENT_PROFILE = "a11b-causal-isolation-v2"
SUCCESSOR_PROFILE = "a11b-successor-development-v1"
ARMS = ("t0", "t1", "e1")
QUESTION_COUNT = 384
ANSWER_CALLS = QUESTION_COUNT * len(ARMS)
SUCCESSOR_QUESTION_COUNT = 64
SUCCESSOR_ANSWER_CALLS = SUCCESSOR_QUESTION_COUNT * len(ARMS)
SUCCESSOR_PUBLIC_MANIFEST_SHA256 = (
    "9bf09379d93db80c430b59a59ca79f522e185de6baef048bed40f29017f3e74d"
)
SUCCESSOR_AUDIT_MANIFEST_SHA256 = (
    "b233b4bdfe9411ccf2720acd3e7850a01f340b73bce46bdc07adda0260362dcc"
)
SUCCESSOR_REGISTERED_SCHEMA_SHA256 = (
    "d33fc4d158865ddc9b3381556b203afd452b93f10829b7d0f743f28beb3d7a05"
)
SUCCESSOR_TRANSPORT_SCHEMA_SHA256 = (
    "e43ccacd51b8f828da38e22e723bf8dcb420c9ca2fac4a440e9ff147ed89760f"
)
ANSWER_MODEL = "gpt-5.6-sol"
ANSWER_EFFORT = "high"
ANSWER_TIMEOUT = 900
MAX_ATTEMPTS = 3
PANEL_MODEL = "gpt-5.6-sol"
PANEL_EFFORT = "high"
PANEL_VOTES = 3
PANEL_BATCH_SIZE = 20
PANEL_TIMEOUT = 600
PRODUCTION_RUNTIME = Path("/usr/local/lib/coralehr-experiment-executor/codex")
PRODUCTION_SNAPSHOT_ROOT = service.PRODUCTION_BUNDLE_DIR / "snapshots"

CODE_SUBJECT_FILES = {
    "a11b_nightly_bootstrap": "a11b_nightly_bootstrap.py",
    "a11b_nightly_runner": "a11b_nightly_runner.py",
    "a11b_launch_protocol": "a11b_launch_protocol.py",
    "anchor": "experiment_anchor.py",
    "bootstrap": "experiment_executor_bootstrap.py",
    "codex_harness": "codex_harness.py",
    "driver": "trusted_codex_driver.py",
    "executor": "experiment_executor.py",
    "service": "experiment_executor_service.py",
    "witness": "experiment_witness.py",
}


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def pretty_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_receipt(path: Path, *, installed_path: Path | None = None) -> dict[str, Any]:
    payload = path.read_bytes()
    receipt: dict[str, Any] = {"sha256": sha256(payload), "bytes": len(payload)}
    if installed_path is not None:
        receipt["path"] = str(installed_path)
    return receipt


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def _verify_manifest_tree(root: Path) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    manifest = _load_json(manifest_path)
    sidecar = (root / "manifest.sha256").read_text(encoding="ascii")
    if sidecar != sha256(manifest_path.read_bytes()) + "\n":
        raise ValueError(f"manifest sidecar changed: {root}")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("corpus manifest has no artifact inventory")
    for relative, expected in artifacts.items():
        if not isinstance(relative, str) or not isinstance(expected, dict):
            raise ValueError("corpus artifact inventory changed")
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"corpus artifact is unavailable: {relative}")
        if file_receipt(path) != expected:
            raise ValueError(f"corpus artifact changed: {relative}")
    return manifest


def _verify_audit_manifest_only(root: Path) -> tuple[dict[str, Any], str]:
    """Verify only the public audit commitment, never its gold artifacts."""

    manifest_path = root / "manifest.json"
    payload = manifest_path.read_bytes()
    digest = sha256(payload)
    if (root / "manifest.sha256").read_text(encoding="ascii") != digest + "\n":
        raise ValueError("audit manifest sidecar changed")
    manifest = json.loads(payload)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "a11b-audit-corpus-manifest-v1"
        or manifest.get("model_calls") != 0
        or manifest.get("split_counts") != {"development": 64, "efficacy": 384}
        or not isinstance(manifest.get("artifacts"), dict)
    ):
        raise ValueError("A11b audit manifest identity changed")
    return manifest, digest


def _verify_successor_audit_manifest_only(
    root: Path,
) -> tuple[dict[str, Any], str]:
    """Verify the successor audit commitment without opening gold or audit rows."""

    manifest_path = root / "manifest.json"
    payload = manifest_path.read_bytes()
    digest = sha256(payload)
    if (root / "manifest.sha256").read_text(encoding="ascii") != digest + "\n":
        raise ValueError("successor audit manifest sidecar changed")
    manifest = json.loads(payload)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version")
        != "a11b-successor-development-audit-manifest-v1"
        or manifest.get("model_calls") != 0
        or manifest.get("split_counts") != {"development": 64}
        or manifest.get("reserved_efficacy_patient_count") != 384
        or manifest.get("efficacy_materialized") is not False
        or not isinstance(manifest.get("artifacts"), dict)
        or any(not path.startswith("development/") for path in manifest["artifacts"])
    ):
        raise ValueError("A11b successor audit manifest identity changed")
    return manifest, digest


def rotating_schedule(question_ids: list[str]) -> list[tuple[str, str]]:
    if len(question_ids) != QUESTION_COUNT or len(set(question_ids)) != QUESTION_COUNT:
        raise ValueError(
            "A11b efficacy split must contain exactly 384 unique questions"
        )
    schedule: list[tuple[str, str]] = []
    for index, question_id in enumerate(question_ids):
        offset = index % len(ARMS)
        arms = ARMS[offset:] + ARMS[:offset]
        schedule.extend((question_id, arm) for arm in arms)
    if len(schedule) != ANSWER_CALLS:
        raise RuntimeError("A11b answer schedule length changed")
    return schedule


def successor_rotating_schedule(question_ids: list[str]) -> list[tuple[str, str]]:
    """Compile the registered 64-patient development schedule only."""

    if (
        len(question_ids) != 64
        or len(set(question_ids)) != 64
        or any(
            not isinstance(question_id, str)
            or not question_id
            or "efficacy" in question_id.casefold()
            for question_id in question_ids
        )
    ):
        raise ValueError(
            "A11b successor development split must contain exactly 64 unique "
            "development questions"
        )
    schedule: list[tuple[str, str]] = []
    for index, question_id in enumerate(question_ids):
        offset = index % len(ARMS)
        arms = ARMS[offset:] + ARMS[:offset]
        schedule.extend((question_id, arm) for arm in arms)
    if len(schedule) != 192:
        raise RuntimeError("A11b successor answer schedule length changed")
    return schedule


def _prompt_inventory(
    public_root: Path,
) -> tuple[list[str], dict[tuple[str, str], bytes]]:
    efficacy = public_root / "efficacy"
    rows = a11_answer_harness.load_input_rows(efficacy / "answer_input.csv")
    question_ids = [row["question_id"] for row in rows]
    by_id = {row["question_id"]: row for row in rows}
    prompts: dict[tuple[str, str], bytes] = {}
    for arm in ARMS:
        records = a11_answer_harness.load_prompt_records(
            efficacy / f"{arm}_packets.jsonl"
        )
        if set(records) != set(question_ids):
            raise ValueError(f"{arm} prompt coverage differs from efficacy questions")
        for question_id in question_ids:
            prompts[(arm, question_id)] = a11_answer_harness.build_verified_prompt(
                by_id[question_id], records[question_id]
            )
    return question_ids, prompts


def _successor_prompt_inventory(
    public_root: Path,
) -> tuple[list[str], dict[tuple[str, str], bytes]]:
    development = public_root / "development"
    rows = a11_answer_harness.load_input_rows(development / "answer_input.csv")
    question_ids = [row["question_id"] for row in rows]
    successor_rotating_schedule(question_ids)
    by_id = {row["question_id"]: row for row in rows}
    prompts: dict[tuple[str, str], bytes] = {}
    for arm in ARMS:
        records = a11_answer_harness.load_successor_prompt_records(
            development / f"{arm}_packets.jsonl"
        )
        if set(records) != set(question_ids):
            raise ValueError(
                f"{arm} successor prompt coverage differs from development questions"
            )
        for question_id in question_ids:
            prompts[(arm, question_id)] = (
                a11_answer_harness.build_verified_successor_prompt(
                    by_id[question_id], records[question_id]
                )
            )
    return question_ids, prompts


def _read_secret(path: Path, *, expected_bytes: int) -> bytes:
    status = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(status.st_mode)
        or status.st_nlink != 1
        or stat.S_IMODE(status.st_mode) not in {0o400, 0o600}
        or status.st_size != expected_bytes
    ):
        raise ValueError(f"sealed secret metadata is invalid: {path.name}")
    return path.read_bytes()


def _code_subjects(source_root: Path) -> list[dict[str, Any]]:
    result = []
    for name, filename in sorted(CODE_SUBJECT_FILES.items()):
        receipt = file_receipt(source_root / filename)
        result.append({"name": name, **receipt})
    return result


def _copy_snapshot(
    output_root: Path,
    *,
    name: str,
    source: Path,
    installed_name: str | None = None,
) -> dict[str, Any]:
    filename = installed_name or source.name
    destination = output_root / "snapshots" / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as handle:
        handle.write(source.read_bytes())
    destination.chmod(0o400)
    return {
        **file_receipt(destination),
        "snapshot_path": str(PRODUCTION_SNAPSHOT_ROOT / filename),
        "logical_name": name,
    }


def build_controller_bundle(
    *,
    source_root: Path,
    public_root: Path,
    audit_root: Path,
    output_root: Path,
    commitment_key_path: Path,
    witness_private_key_path: Path,
    runtime_source: Path,
    runtime_version: str,
    python_tree_receipt_path: Path,
    install_manifest_path: Path,
    ssh_keygen_receipt_path: Path,
    sandbox_receipt_path: Path,
    preregistration_path: Path,
    answer_schema_path: Path,
    validation_schema_path: Path | None = None,
    experiment_profile: str = EXPERIMENT_PROFILE,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_root = source_root.resolve()
    public_root = public_root.resolve()
    audit_root = audit_root.resolve()
    output_root = Path(os.path.abspath(output_root))
    if output_root.exists() or output_root.is_symlink():
        raise FileExistsError(output_root)
    if experiment_profile not in {EXPERIMENT_PROFILE, SUCCESSOR_PROFILE}:
        raise ValueError("A11b controller profile is not registered")
    successor = experiment_profile == SUCCESSOR_PROFILE
    question_count = SUCCESSOR_QUESTION_COUNT if successor else QUESTION_COUNT
    answer_calls = SUCCESSOR_ANSWER_CALLS if successor else ANSWER_CALLS
    public_manifest = _verify_manifest_tree(public_root)
    if successor:
        audit_manifest, audit_manifest_sha256 = _verify_successor_audit_manifest_only(
            audit_root
        )
    else:
        audit_manifest, audit_manifest_sha256 = _verify_audit_manifest_only(audit_root)
    public_manifest_sha256 = sha256((public_root / "manifest.json").read_bytes())
    if public_manifest.get("contains_gold") is not False:
        raise ValueError("A11b public corpus is not blind")
    if successor:
        if (
            public_manifest.get("schema_version")
            != "a11b-successor-development-public-manifest-v1"
            or public_manifest.get("split_counts") != {"development": 64}
            or public_manifest.get("reserved_efficacy_patient_count") != 384
            or public_manifest.get("efficacy_materialized") is not False
            or any(
                not path.startswith("development/")
                for path in public_manifest.get("artifacts", {})
            )
            or public_manifest_sha256 != SUCCESSOR_PUBLIC_MANIFEST_SHA256
            or audit_manifest_sha256 != SUCCESSOR_AUDIT_MANIFEST_SHA256
        ):
            raise ValueError("A11b successor development corpus identity changed")
    else:
        if public_manifest.get("split_counts") != {
            "development": 64,
            "efficacy": 384,
        }:
            raise ValueError("A11b public split counts changed")
        if public_manifest_sha256 != a11b_grading.REGISTERED_DATASET_MANIFEST_SHA256:
            raise ValueError("A11b public corpus differs from the registered manifest")
        if audit_manifest_sha256 != a11b_grading.REGISTERED_AUDIT_MANIFEST_SHA256:
            raise ValueError("A11b audit corpus differs from the registered manifest")
    if audit_manifest.get("public_manifest_sha256") != public_manifest_sha256:
        raise ValueError("A11b audit/public corpus binding changed")
    if successor:
        question_ids, prompts = _successor_prompt_inventory(public_root)
        schedule_hosts = successor_rotating_schedule(question_ids)
    else:
        question_ids, prompts = _prompt_inventory(public_root)
        schedule_hosts = rotating_schedule(question_ids)
    schema_bytes = answer_schema_path.read_bytes()
    schema = json.loads(schema_bytes)
    if schema.get("additionalProperties") is not False:
        raise ValueError("A11b answer schema must reject additional properties")
    if successor:
        validation_schema_path = validation_schema_path or answer_schema_path
        validation_schema_bytes = validation_schema_path.read_bytes()
        if (
            sha256(schema_bytes) != SUCCESSOR_TRANSPORT_SCHEMA_SHA256
            or sha256(validation_schema_bytes) != SUCCESSOR_REGISTERED_SCHEMA_SHA256
        ):
            raise ValueError("A11b successor answer schema identity changed")
    else:
        validation_schema_path = answer_schema_path
        validation_schema_bytes = schema_bytes

    commitment_key = _read_secret(commitment_key_path, expected_bytes=32)
    runtime_payload = runtime_source.read_bytes()
    runtime = {
        "path": str(PRODUCTION_RUNTIME),
        "sha256": sha256(runtime_payload),
        "bytes": len(runtime_payload),
        "version": runtime_version,
    }
    if runtime_version != "codex-cli 0.144.1":
        raise ValueError("A11b runtime version differs from the preregistered pin")
    python_tree_payload = python_tree_receipt_path.read_bytes()
    python_tree = _load_json(python_tree_receipt_path)
    if service.canonical_json_line(python_tree) != python_tree_payload:
        raise ValueError("trusted Python tree receipt is noncanonical")
    install_manifest_payload = install_manifest_path.read_bytes()
    try:
        install_manifest = install.validate_install_manifest(
            json.loads(install_manifest_payload)
        )
    except (json.JSONDecodeError, install.InstallProtocolError) as exc:
        raise ValueError("trusted install manifest changed") from exc
    if service.canonical_json_line(install_manifest) != install_manifest_payload:
        raise ValueError("trusted install manifest is noncanonical")
    if install_manifest["python_runtime"] != python_tree:
        raise ValueError("install manifest Python tree binding changed")
    for name, filename in sorted(install._CODE_FILES.items()):
        source_receipt = file_receipt(source_root / filename)
        installed_receipt = install_manifest["code_subjects"][name]
        if source_receipt != {
            "sha256": installed_receipt["sha256"],
            "bytes": installed_receipt["bytes"],
        }:
            raise ValueError("install manifest service code binding changed")
    python_tree_file_receipt = file_receipt(python_tree_receipt_path)
    install_manifest_file_receipt = file_receipt(install_manifest_path)
    python_entry = next(
        (
            entry
            for entry in python_tree.get("entries", [])
            if entry.get("path") == "bin/python3.14"
        ),
        None,
    )
    if not isinstance(python_entry, dict):
        raise ValueError("trusted Python executable is absent from its tree receipt")
    python_receipt = {
        "path": str(service.PRODUCTION_PYTHON_PATH),
        "sha256": python_entry["sha256"],
        "bytes": python_entry["bytes"],
    }
    ssh_keygen_receipt = _load_json(ssh_keygen_receipt_path)
    if ssh_keygen_receipt.get("path") != str(witness.SSH_KEYGEN_PATH):
        raise ValueError("trusted ssh-keygen path changed")
    sandbox = _load_json(sandbox_receipt_path)
    if (
        not isinstance(sandbox.get("sha256"), str)
        or len(sandbox["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in sandbox["sha256"])
        or type(sandbox.get("bytes")) is not int
        or sandbox["bytes"] <= 0
        or sandbox
        != {
            "path": "/usr/bin/sandbox-exec",
            "sha256": sandbox.get("sha256"),
            "bytes": sandbox.get("bytes"),
            "profile": trusted_codex_driver.TrustedCodexDriver._SANDBOX_PROFILE,
        }
    ):
        raise ValueError("trusted sandbox receipt changed")
    model_configuration = {
        "answer": {
            "model": ANSWER_MODEL,
            "reasoning_effort": ANSWER_EFFORT,
            "timeout_seconds": ANSWER_TIMEOUT,
        }
    }
    if not successor:
        model_configuration["panel"] = {
            "model": PANEL_MODEL,
            "reasoning_effort": PANEL_EFFORT,
            "votes": PANEL_VOTES,
            "batch_size": PANEL_BATCH_SIZE,
            "timeout_seconds": PANEL_TIMEOUT,
        }
    authenticator = witness.SshEd25519Authenticator(
        private_key_path=witness_private_key_path,
        identity=(
            "a11b-successor-development-witness-2026-07-18"
            if successor
            else "a11b-witness-2026-07-16"
        ),
    )
    schedule_binding_sha256 = sha256(
        canonical_json(
            [
                {
                    "schedule_index": index,
                    "question_id": question_id,
                    "arm": arm,
                    "prompt_sha256": sha256(prompts[(arm, question_id)]),
                }
                for index, (question_id, arm) in enumerate(schedule_hosts)
            ]
        )
    )
    run_id_preimage = {
        "schema_version": "a11b-run-id-preimage-v2",
        "profile": experiment_profile,
        "public_manifest_sha256": public_manifest_sha256,
        "audit_manifest_sha256": audit_manifest_sha256,
        "code_subjects": _code_subjects(source_root),
        "postprocessor": file_receipt(
            source_root
            / (
                "a11b_successor_development_postprocess.py"
                if successor
                else "a11b_postprocess.py"
            )
        ),
        "preregistration": file_receipt(preregistration_path),
        "answer_schema": file_receipt(answer_schema_path),
        "model_configuration": model_configuration,
        "schedule_binding_sha256": schedule_binding_sha256,
        "runtime": runtime,
        "sandbox": sandbox,
        "python": python_receipt,
        "python_tree": python_tree_file_receipt,
        "install_manifest": install_manifest_file_receipt,
        "ssh_keygen": ssh_keygen_receipt,
        "witness_key_id": authenticator.key_id,
    }
    if successor:
        run_id_preimage["validation_schema"] = file_receipt(validation_schema_path)
    run_id = sha256(canonical_json(run_id_preimage))
    invocations = []
    witness_schedule = []
    host_schedule = []
    for index, (question_id, arm) in enumerate(schedule_hosts):
        prompt = prompts[(arm, question_id)]
        invocation = experiment_executor.SealedInvocation(
            phase="answer",
            schedule_index=index,
            prompt=prompt,
            output_schema=schema_bytes,
            model=ANSWER_MODEL,
            reasoning_effort=ANSWER_EFFORT,
            runtime_path=str(PRODUCTION_RUNTIME),
            runtime_sha256=runtime["sha256"],
            timeout_seconds=ANSWER_TIMEOUT,
        )
        invocations.append(
            {
                "phase": "answer",
                "schedule_index": index,
                "prompt_base64": base64.b64encode(prompt).decode("ascii"),
                "output_schema_base64": base64.b64encode(schema_bytes).decode("ascii"),
                "model": ANSWER_MODEL,
                "reasoning_effort": ANSWER_EFFORT,
                "timeout_seconds": ANSWER_TIMEOUT,
            }
        )
        witness_schedule.append(
            {
                "phase": "answer",
                "schedule_index": index,
                "call_commitment": invocation.call_commitment(commitment_key),
                "max_attempts": MAX_ATTEMPTS,
            }
        )
        host_schedule.append(
            {
                "schedule_index": index,
                "arm": arm,
                "question_id": question_id,
                "prompt_sha256": sha256(prompt),
            }
        )
    bundle = {
        "kind": "experiment_executor_service_bundle",
        "schema_version": service.BUNDLE_SCHEMA_VERSION,
        "service_protocol_version": service.SERVICE_SCHEMA_VERSION,
        "run_id": run_id,
        "witness": {
            "identity": authenticator.identity,
            "public_key": authenticator.public_key,
            "key_id": authenticator.key_id,
            "schedule": witness_schedule,
        },
        "runtime": runtime,
        "sandbox": sandbox,
        "executables": {
            "python": python_receipt,
            "ssh_keygen": ssh_keygen_receipt,
        },
        "code_subjects": _code_subjects(source_root),
        "model_configuration": model_configuration,
        "anchor_verifier": service.ANCHOR_CHECKER_VERIFIER,
        "invocations": invocations,
    }
    bundle_bytes = service.canonical_json_line(bundle)
    bundle_commitment = witness.keyed_commitment(
        commitment_key,
        domain="executor-bundle",
        payload=bundle_bytes,
    )
    trusted_executor = service._public_binding(
        bundle,
        bundle_commitment=bundle_commitment,
    )

    output_root.mkdir(mode=0o700)
    packet_split = "development" if successor else "efficacy"
    snapshots = {
        "preregistration": _copy_snapshot(
            output_root,
            name="preregistration",
            source=preregistration_path,
            installed_name=(
                "A11B_SUCCESSOR_DEVELOPMENT_PREREGISTRATION.md"
                if successor
                else "A11B_PREREGISTRATION.md"
            ),
        ),
        "packet_v": _copy_snapshot(
            output_root,
            name="packet_t0",
            source=public_root / packet_split / "t0_packets.jsonl",
        ),
        "packet_t": _copy_snapshot(
            output_root,
            name="packet_t1",
            source=public_root / packet_split / "t1_packets.jsonl",
        ),
        "packet_e": _copy_snapshot(
            output_root,
            name="packet_e1",
            source=public_root / packet_split / "e1_packets.jsonl",
        ),
        "schema": _copy_snapshot(
            output_root,
            name="answer_schema",
            source=answer_schema_path,
        ),
        "answer_input": _copy_snapshot(
            output_root,
            name="answer_input",
            source=public_root / packet_split / "answer_input.csv",
        ),
    }
    if successor:
        snapshots["registered_schema"] = _copy_snapshot(
            output_root,
            name="registered_answer_schema",
            source=validation_schema_path,
        )
    snapshot_code = (
        {
            "a11_evidence_core": "a11_evidence_core.py",
            "a11b_answer_contract": "a11b_answer_contract.py",
            "a11b_postprocess": "a11b_postprocess.py",
            "a11b_successor_dev_gate": "a11b_successor_dev_gate.py",
            "a11b_successor_development_grading": (
                "a11b_successor_development_grading.py"
            ),
            "a11b_successor_development_postprocess": (
                "a11b_successor_development_postprocess.py"
            ),
            "a11b_nightly_bootstrap": "a11b_nightly_bootstrap.py",
            "a11b_nightly_runner": "a11b_nightly_runner.py",
            "run_lock": "run_lock.py",
        }
        if successor
        else {
            "a11_grading": "a11b_grading.py",
            "a11b_postprocess": "a11b_postprocess.py",
            "a11b_nightly_bootstrap": "a11b_nightly_bootstrap.py",
            "a11b_nightly_runner": "a11b_nightly_runner.py",
            "run_a11_panel": "run_a11b_panel.py",
            "panel_grade": "panel_grade.py",
            "run_lock": "run_lock.py",
            "paired_stats": "paired_stats.py",
        }
    )
    for name, filename in {
        "experiment_anchor": "experiment_anchor.py",
        "codex_harness": "codex_harness.py",
        **snapshot_code,
    }.items():
        snapshots[name] = _copy_snapshot(
            output_root,
            name=name,
            source=source_root / filename,
        )
    for name, source in {
        "public_manifest": public_root / "manifest.json",
        "audit_manifest": audit_root / "manifest.json",
        "python_tree": python_tree_receipt_path,
        "install_manifest": install_manifest_path,
    }.items():
        snapshots[name] = _copy_snapshot(
            output_root,
            name=name,
            source=source,
            installed_name={
                "public_manifest": "public_manifest.json",
                "audit_manifest": "audit_manifest.json",
                "python_tree": "python-tree-receipt.json",
                "install_manifest": "install-manifest.json",
            }[name],
        )

    codex = {**runtime, "native": {k: runtime[k] for k in ("path", "sha256", "bytes")}}
    if successor:
        grading = {
            "method": "a11b-successor-development-exact-alias-grading-v1",
            "panel_model_calls": 0,
            "registered_schema_sha256": snapshots["registered_schema"]["sha256"],
            "transport_schema_sha256": snapshots["schema"]["sha256"],
            "grading_source_sha256": snapshots["a11b_successor_development_grading"][
                "sha256"
            ],
            "gate_source_sha256": snapshots["a11b_successor_dev_gate"]["sha256"],
            "postprocess_source_sha256": snapshots[
                "a11b_successor_development_postprocess"
            ]["sha256"],
        }
    else:
        panel_source_sha = snapshots["run_a11_panel"]["sha256"]
        grading = a11b_grading.registered_analysis_config(
            codex_bin=str(PRODUCTION_RUNTIME),
            codex_version=runtime_version,
            codex_binary_sha256=runtime["sha256"],
            answer_schema_sha256=snapshots["schema"]["sha256"],
            panel_source_sha256=panel_source_sha,
            grading_source_sha256=snapshots["a11_grading"]["sha256"],
        )
        grading["panel"] = {
            **model_configuration["panel"],
            "codex_bin": str(PRODUCTION_RUNTIME),
            "codex_version": runtime_version,
            "codex_binary_sha256": runtime["sha256"],
            "panel_source_sha256": panel_source_sha,
        }
    controller_inputs = {
        "public_manifest_sha256": public_manifest_sha256,
        "audit_manifest_sha256": audit_manifest_sha256,
        "python_tree_receipt_sha256": python_tree_file_receipt["sha256"],
        "install_manifest_sha256": install_manifest_file_receipt["sha256"],
        "question_count": question_count,
        "answer_calls": answer_calls,
        "gold_opened_before_completion": False,
    }
    if successor:
        controller_inputs.update(
            {
                "reserved_efficacy_patient_count": 384,
                "efficacy_materialized": False,
            }
        )
    controller_outputs = {
        "answer_export": str(service.PRODUCTION_BUNDLE_DIR / "results/answer-export"),
        "grading": str(service.PRODUCTION_BUNDLE_DIR / "results/grading"),
        "result": str(service.PRODUCTION_BUNDLE_DIR / "results/final"),
    }
    if not successor:
        controller_outputs["panel"] = str(
            service.PRODUCTION_BUNDLE_DIR / "results/panel"
        )
    controller = {
        "kind": "a11_interleaved_controller_manifest",
        "schema_version": CONTROLLER_VERSION,
        "experiment_profile": experiment_profile,
        "run_id": run_id,
        "execution": {
            **model_configuration["answer"],
            "codex": codex,
            "trusted_executor": trusted_executor,
        },
        "grading": grading,
        "inputs": controller_inputs,
        "schedule": {
            "method": "rotating-interleaved-v1",
            "arms": list(ARMS),
            "items": host_schedule,
        },
        "snapshots": snapshots,
        "outputs": controller_outputs,
        "model_calls_at_seal": 0,
    }
    controller_bytes = pretty_json(controller)
    # Prove the public controller is acceptable to the external anchor compiler.
    controller_path = output_root / "controller.json"
    controller_path.write_bytes(controller_bytes)
    controller_path.chmod(0o400)
    (output_root / "controller.sha256").write_text(
        sha256(controller_bytes) + "\n", encoding="ascii"
    )
    (output_root / "controller.sha256").chmod(0o400)
    anchor_request = experiment_anchor.build_anchor_request(controller_path)
    (output_root / "anchor-request.json").write_bytes(canonical_json(anchor_request))
    (output_root / "anchor-request.json").chmod(0o400)
    (output_root / "bundle.json").write_bytes(bundle_bytes)
    (output_root / "bundle.json").chmod(0o400)
    shutil.copyfile(commitment_key_path, output_root / "commitment.key")
    (output_root / "commitment.key").chmod(0o600)
    shutil.copyfile(witness_private_key_path, output_root / "witness_ed25519")
    (output_root / "witness_ed25519").chmod(0o600)
    shutil.copyfile(runtime_source, output_root / "codex")
    (output_root / "codex").chmod(0o500)
    shutil.copyfile(python_tree_receipt_path, output_root / "python-tree-receipt.json")
    (output_root / "python-tree-receipt.json").chmod(0o400)
    shutil.copyfile(install_manifest_path, output_root / "install-manifest.json")
    (output_root / "install-manifest.json").chmod(0o400)
    return controller, bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--commitment-key", type=Path, required=True)
    parser.add_argument("--witness-private-key", type=Path, required=True)
    parser.add_argument("--runtime-source", type=Path, required=True)
    parser.add_argument("--runtime-version", required=True)
    parser.add_argument("--python-tree-receipt", type=Path, required=True)
    parser.add_argument("--install-manifest", type=Path, required=True)
    parser.add_argument("--ssh-keygen-receipt", type=Path, required=True)
    parser.add_argument("--sandbox-receipt", type=Path, required=True)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--answer-schema", type=Path, required=True)
    parser.add_argument("--validation-schema", type=Path)
    parser.add_argument(
        "--experiment-profile",
        choices=(EXPERIMENT_PROFILE, SUCCESSOR_PROFILE),
        default=EXPERIMENT_PROFILE,
    )
    args = parser.parse_args()
    controller, bundle = build_controller_bundle(
        source_root=args.source_root,
        public_root=args.public_root,
        audit_root=args.audit_root,
        output_root=args.output_root,
        commitment_key_path=args.commitment_key,
        witness_private_key_path=args.witness_private_key,
        runtime_source=args.runtime_source,
        runtime_version=args.runtime_version,
        python_tree_receipt_path=args.python_tree_receipt,
        install_manifest_path=args.install_manifest,
        ssh_keygen_receipt_path=args.ssh_keygen_receipt,
        sandbox_receipt_path=args.sandbox_receipt,
        preregistration_path=args.preregistration,
        answer_schema_path=args.answer_schema,
        validation_schema_path=args.validation_schema,
        experiment_profile=args.experiment_profile,
    )
    print(
        json.dumps(
            {
                "controller_sha256": sha256(pretty_json(controller)),
                "bundle_sha256": sha256(service.canonical_json_line(bundle)),
                "answer_calls": controller["inputs"]["answer_calls"],
                "model_calls": 0,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
