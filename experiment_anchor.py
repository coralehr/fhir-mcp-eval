#!/usr/bin/env python3
"""Build and verify pre-answer experiment anchors outside the run host."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlsplit
from urllib.request import Request, urlopen


ANCHOR_REQUEST_VERSION = "experiment-external-anchor-v1"
ANCHOR_REQUEST_VERSION_V2 = "experiment-external-anchor-v2"
ANCHOR_REQUEST_KIND = "experiment_external_anchor_request"
ANCHOR_VERIFICATION_VERSION = "experiment-external-anchor-verification-v1"
SIGNED_ANCHOR_VERIFICATION_VERSION = "experiment-external-anchor-verification-v2"
SIGNED_ANCHOR_VERIFICATION_KIND = "experiment_external_anchor_signed_verification"
SIGNED_ANCHOR_NAMESPACE = "coralehr-experiment-anchor-verification-v2"
SIGNED_ANCHOR_DOMAIN = b"coralehr-experiment-anchor-verification-v2\0"
SSH_KEYGEN_PATH = Path("/usr/bin/ssh-keygen")
SSH_KEYGEN_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
SSH_KEYGEN_TIMEOUT_SECONDS = 30
MAX_REMOTE_BYTES = 1024 * 1024
TRUSTED_REVIEWER_ASSOCIATIONS = frozenset({"MEMBER", "OWNER", "COLLABORATOR"})
TRUSTED_INDEPENDENT_APPROVERS_BY_ID: Mapping[int, str] = MappingProxyType(
    {
        143709176: "Arhaan2104",
        143727238: "AJ112103",
    }
)
TRUSTED_INDEPENDENT_APPROVERS = frozenset(
    TRUSTED_INDEPENDENT_APPROVERS_BY_ID.values()
)
_SANDBOX_ROOT = "/Library/Application Support/CoralEHR/experiment-executor"
_SANDBOX_DENIED_SUBPATHS = ("audit-input", "state", "results", "snapshots")
_SANDBOX_DENIED_FILES = (
    "controller.json",
    "controller.sha256",
    "bundle.json",
    "commitment.key",
    "witness_ed25519",
    "python-tree-receipt.json",
    "external-anchor-verification.json",
    "anchor-locator.json",
    "install-manifest.json",
    "nightly-status.json",
    "nightly-runner.log",
    "nightly-runner.lock",
)
EXPECTED_SANDBOX_PROFILE = (
    "(version 1)(allow default)(deny process-fork)"
    + "".join(
        f'(deny file-read* (subpath "{_SANDBOX_ROOT}/{relative}"))'
        for relative in _SANDBOX_DENIED_SUBPATHS
    )
    + "".join(
        f'(deny file-read* (literal "{_SANDBOX_ROOT}/{relative}"))'
        for relative in _SANDBOX_DENIED_FILES
    )
)


def _anchor_verifier(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "algorithm",
        "identity",
        "namespace",
        "public_key",
        "key_id",
    }:
        raise ValueError("anchor verifier fields are invalid")
    identity = value.get("identity")
    public_key = value.get("public_key")
    if (
        value.get("algorithm") != "ssh-ed25519"
        or value.get("namespace") != SIGNED_ANCHOR_NAMESPACE
        or not isinstance(identity, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}", identity) is None
        or not isinstance(public_key, str)
        or re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]+={0,2}", public_key) is None
    ):
        raise ValueError("anchor verifier identity is invalid")
    key_id = "sha256:" + sha256_bytes((public_key + "\n").encode("ascii"))
    if value.get("key_id") != key_id:
        raise ValueError("anchor verifier key identity is invalid")
    return {
        "algorithm": "ssh-ed25519",
        "identity": identity,
        "namespace": SIGNED_ANCHOR_NAMESPACE,
        "public_key": public_key,
        "key_id": key_id,
    }

_SNAPSHOT_SUBJECTS = {
    "preregistration": "preregistration",
    "packet_v": "packet_v",
    "packet_t": "packet_t",
    "packet_e": "packet_e",
    "answer_schema": "schema",
    "a11_grading": "a11_grading",
    "run_a11_panel": "run_a11_panel",
    "panel_grade": "panel_grade",
}
_CONTENTS_PATH = re.compile(
    r"^/repos/coralehr/fhir-mcp-eval/contents/(anchors/[A-Za-z0-9._/-]+\.json)$"
)


@dataclass(frozen=True)
class GitHubAnchorLocator:
    contents_url: str
    commit_url: str
    commit_sha: str
    path: str


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
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


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _receipt(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"anchor subject is missing: {label}")
    digest = value.get("sha256")
    size = value.get("bytes")
    if not _is_sha256(digest) or type(size) is not int or size < 0:
        raise ValueError(f"anchor subject receipt is malformed: {label}")
    return {"sha256": digest, "bytes": size}


def _path_receipt(
    value: object, *, label: str, extra_fields: frozenset[str] = frozenset()
) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {
        "path",
        "sha256",
        "bytes",
        *extra_fields,
    }:
        raise ValueError(f"trusted executor subject is malformed: {label}")
    path = value.get("path")
    receipt = _receipt(value, label=label)
    if (
        not isinstance(path, str)
        or not Path(path).is_absolute()
        or ".." in Path(path).parts
        or "//" in path
    ):
        raise ValueError(f"trusted executor subject path is malformed: {label}")
    return {"path": path, **receipt}


def _model_configuration(value: object) -> dict[str, dict[str, object]]:
    if not isinstance(value, Mapping) or set(value) != {"answer", "panel"}:
        raise ValueError("model configuration fields are invalid")
    answer = value.get("answer")
    panel = value.get("panel")
    if not isinstance(answer, Mapping) or set(answer) != {
        "model",
        "reasoning_effort",
        "timeout_seconds",
    }:
        raise ValueError("answer model configuration is invalid")
    if not isinstance(panel, Mapping) or set(panel) != {
        "model",
        "reasoning_effort",
        "votes",
        "batch_size",
        "timeout_seconds",
    }:
        raise ValueError("panel model configuration is invalid")

    def require_model(item: Mapping[str, object], label: str) -> str:
        model = item.get("model")
        if not isinstance(model, str) or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", model
        ) is None:
            raise ValueError(f"{label} model configuration is invalid")
        return model

    def require_reasoning(item: Mapping[str, object], label: str) -> str:
        reasoning = item.get("reasoning_effort")
        if reasoning not in {"minimal", "low", "medium", "high", "xhigh"}:
            raise ValueError(f"{label} model configuration is invalid")
        return reasoning

    def require_positive_integer(
        item: Mapping[str, object], field: str, label: str, maximum: int
    ) -> int:
        result = item.get(field)
        if type(result) is not int or not 1 <= result <= maximum:
            raise ValueError(f"{label} model configuration is invalid")
        return result

    return {
        "answer": {
            "model": require_model(answer, "answer"),
            "reasoning_effort": require_reasoning(answer, "answer"),
            "timeout_seconds": require_positive_integer(
                answer, "timeout_seconds", "answer", 3600
            ),
        },
        "panel": {
            "model": require_model(panel, "panel"),
            "reasoning_effort": require_reasoning(panel, "panel"),
            "votes": require_positive_integer(panel, "votes", "panel", 9),
            "batch_size": require_positive_integer(
                panel, "batch_size", "panel", 1000
            ),
            "timeout_seconds": require_positive_integer(
                panel, "timeout_seconds", "panel", 3600
            ),
        },
    }


def _trusted_executor_binding(
    value: object,
    *,
    expected_code_names: tuple[str, ...] = (
        "anchor",
        "bootstrap",
        "codex_harness",
        "driver",
        "executor",
        "service",
        "witness",
    ),
) -> dict[str, Any]:
    fields = {
        "bundle_commitment",
        "bundle_schema_version",
        "service_protocol_version",
        "run_id",
        "witness",
        "runtime",
        "sandbox",
        "executables",
        "code_subjects",
        "model_configuration",
        "anchor_verifier",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("trusted executor binding fields are invalid")
    if (
        not _is_sha256(value.get("bundle_commitment"))
        or value.get("bundle_schema_version")
        != "experiment-executor-service-bundle-v1"
        or value.get("service_protocol_version")
        != "experiment-executor-service-v1"
        or not _is_sha256(value.get("run_id"))
    ):
        raise ValueError("trusted executor binding identity is invalid")

    witness_value = value.get("witness")
    if not isinstance(witness_value, Mapping) or set(witness_value) != {
        "identity",
        "public_key",
        "key_id",
        "schedule",
    }:
        raise ValueError("trusted executor witness binding is invalid")
    identity = witness_value.get("identity")
    public_key = witness_value.get("public_key")
    schedule_value = witness_value.get("schedule")
    if (
        not isinstance(identity, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}", identity) is None
        or not isinstance(public_key, str)
        or re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+", public_key) is None
        or re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(witness_value.get("key_id") or "")
        )
        is None
        or not isinstance(schedule_value, list)
        or not schedule_value
    ):
        raise ValueError("trusted executor witness binding is invalid")
    schedule: list[dict[str, Any]] = []
    phase_position = 0
    last_index: dict[str, int] = {}
    for item in schedule_value:
        if not isinstance(item, Mapping) or set(item) != {
            "phase",
            "schedule_index",
            "call_commitment",
            "max_attempts",
        }:
            raise ValueError("trusted executor schedule fields are invalid")
        phase = item.get("phase")
        index = item.get("schedule_index")
        attempts = item.get("max_attempts")
        if phase not in {"answer", "panel"}:
            raise ValueError("trusted executor schedule phase is invalid")
        new_phase_position = ("answer", "panel").index(phase)
        expected_index = last_index.get(phase, -1) + 1
        if (
            new_phase_position < phase_position
            or type(index) is not int
            or index != expected_index
            or not _is_sha256(item.get("call_commitment"))
            or type(attempts) is not int
            or not 1 <= attempts <= 100
        ):
            raise ValueError("trusted executor schedule is invalid")
        phase_position = new_phase_position
        last_index[phase] = index
        schedule.append(dict(item))

    runtime_value = value.get("runtime")
    if not isinstance(runtime_value, Mapping) or set(runtime_value) != {
        "path",
        "sha256",
        "bytes",
        "version",
    }:
        raise ValueError("trusted executor runtime binding is invalid")
    runtime_version = runtime_value.get("version")
    if not isinstance(runtime_version, str) or re.fullmatch(
        r"codex-cli [A-Za-z0-9][A-Za-z0-9._+-]{0,63}", runtime_version
    ) is None:
        raise ValueError("trusted executor runtime version is invalid")
    runtime = {
        **_path_receipt(
            runtime_value, label="runtime", extra_fields=frozenset({"version"})
        ),
        "version": runtime_version,
    }

    sandbox_value = value.get("sandbox")
    if not isinstance(sandbox_value, Mapping) or set(sandbox_value) != {
        "path",
        "sha256",
        "bytes",
        "profile",
    }:
        raise ValueError("trusted executor sandbox binding is invalid")
    profile = sandbox_value.get("profile")
    if profile != EXPECTED_SANDBOX_PROFILE:
        raise ValueError("trusted executor sandbox profile is invalid")
    sandbox = {
        **_path_receipt(
            sandbox_value, label="sandbox", extra_fields=frozenset({"profile"})
        ),
        "profile": profile,
    }

    executable_value = value.get("executables")
    if not isinstance(executable_value, Mapping) or set(executable_value) != {
        "python",
        "ssh_keygen",
    }:
        raise ValueError("trusted executor executable binding is invalid")
    executables = {
        name: _path_receipt(executable_value.get(name), label=name)
        for name in ("python", "ssh_keygen")
    }

    code_value = value.get("code_subjects")
    if not isinstance(code_value, list) or len(code_value) != len(
        expected_code_names
    ):
        raise ValueError("trusted executor code binding is invalid")
    code_subjects: list[dict[str, object]] = []
    for expected_name, subject in zip(expected_code_names, code_value, strict=True):
        if not isinstance(subject, Mapping) or set(subject) != {
            "name",
            "sha256",
            "bytes",
        }:
            raise ValueError("trusted executor code subject is malformed")
        if subject.get("name") != expected_name:
            raise ValueError("trusted executor code subject order is invalid")
        receipt = _receipt(subject, label=f"code:{expected_name}")
        code_subjects.append({"name": expected_name, **receipt})

    return {
        "bundle_commitment": value["bundle_commitment"],
        "bundle_schema_version": value["bundle_schema_version"],
        "service_protocol_version": value["service_protocol_version"],
        "run_id": value["run_id"],
        "witness": {
            "identity": identity,
            "public_key": public_key,
            "key_id": witness_value["key_id"],
            "schedule": schedule,
        },
        "runtime": runtime,
        "sandbox": sandbox,
        "executables": executables,
        "code_subjects": code_subjects,
        "model_configuration": _model_configuration(value.get("model_configuration")),
        "anchor_verifier": _anchor_verifier(value.get("anchor_verifier")),
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key in external anchor metadata: {key}")
        value[key] = child
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number in external anchor metadata: {value}")


def _load_json_object(payload: bytes, *, label: str) -> dict[str, Any]:
    value = _load_json_value(payload, label=label)
    if not isinstance(value, dict):
        raise ValueError(f"JSON in {label} is not an object")
    return value


def _load_json_value(payload: bytes, *, label: str) -> Any:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON in {label}") from exc
    return value


def parse_github_anchor_url(url: str) -> GitHubAnchorLocator:
    """Accept only a commit-pinned anchor in the trusted public repository."""

    parsed = urlsplit(url)
    match = _CONTENTS_PATH.fullmatch(parsed.path)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.github.com"
        or parsed.fragment
        or match is None
        or len(query) != 1
        or query[0][0] != "ref"
        or re.fullmatch(r"[0-9a-f]{40}", query[0][1]) is None
    ):
        raise ValueError("external anchor URL is not a trusted commit-pinned locator")
    path = match.group(1)
    if ".." in path.split("/") or "//" in path:
        raise ValueError("external anchor path is not canonical")
    commit_sha = query[0][1]
    return GitHubAnchorLocator(
        contents_url=url,
        commit_url=(
            "https://api.github.com/repos/coralehr/fhir-mcp-eval/commits/"
            + commit_sha
        ),
        commit_sha=commit_sha,
        path=path,
    )


def fetch_github_bytes(url: str, accept: str) -> bytes:
    """Fetch one bounded GitHub API response without credentials."""

    request = Request(
        url,
        headers={
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "coralehr-fhir-mcp-eval-anchor/1",
        },
    )
    with urlopen(request, timeout=20) as response:
        final = urlsplit(response.geturl())
        if final.scheme != "https" or final.netloc != "api.github.com":
            raise ValueError("external anchor fetch left the trusted GitHub API host")
        payload = response.read(MAX_REMOTE_BYTES + 1)
    if len(payload) > MAX_REMOTE_BYTES:
        raise ValueError("external anchor response exceeds the registered byte cap")
    return payload


def _github_api_url(path: str) -> str:
    return "https://api.github.com/repos/coralehr/fhir-mcp-eval" + path


def _independent_pr_approval(
    locator: GitHubAnchorLocator,
    *,
    expected_anchor_bytes: bytes,
    fetch_bytes: Callable[[str, str], bytes],
) -> dict[str, Any]:
    pulls: list[Any] = []
    for page in range(1, 11):
        page_pulls = _load_json_value(
            fetch_bytes(
                _github_api_url(
                    f"/commits/{locator.commit_sha}/pulls?per_page=100&page={page}"
                ),
                "application/vnd.github+json",
            ),
            label="external anchor pull-request metadata",
        )
        if not isinstance(page_pulls, list):
            raise ValueError("external anchor pull-request metadata is malformed")
        pulls.extend(page_pulls)
        if len(page_pulls) < 100:
            break
    else:
        raise ValueError("external anchor pull-request metadata exceeds page cap")
    candidates = [
        pull
        for pull in pulls
        if isinstance(pull, dict)
        and pull.get("state") == "closed"
        and isinstance(pull.get("merged_at"), str)
        and pull.get("merged_at")
        and pull.get("merge_commit_sha") == locator.commit_sha
        and isinstance(pull.get("base"), dict)
        and pull["base"].get("ref") == "main"
    ]
    if len(candidates) != 1:
        raise ValueError("external anchor is not one uniquely merged main-branch PR")
    candidate = candidates[0]
    number = candidate.get("number")
    if type(number) is not int or number <= 0:
        raise ValueError("external anchor pull-request metadata is malformed")
    pull = _load_json_object(
        fetch_bytes(
            _github_api_url(f"/pulls/{number}"),
            "application/vnd.github+json",
        ),
        label="external anchor full pull-request metadata",
    )
    head = pull.get("head")
    author = pull.get("user")
    merger = pull.get("merged_by")
    html_url = pull.get("html_url")
    if (
        pull.get("number") != number
        or pull.get("state") != candidate.get("state")
        or pull.get("merged_at") != candidate.get("merged_at")
        or pull.get("merge_commit_sha") != locator.commit_sha
        or pull.get("merge_commit_sha") != candidate.get("merge_commit_sha")
        or not isinstance(candidate.get("base"), dict)
        or not isinstance(pull.get("base"), dict)
        or pull["base"].get("ref") != candidate["base"].get("ref")
        or not isinstance(candidate.get("head"), dict)
        or not isinstance(pull.get("head"), dict)
        or pull["head"].get("sha") != candidate["head"].get("sha")
        or not isinstance(candidate.get("user"), dict)
        or not isinstance(pull.get("user"), dict)
        or pull["user"].get("login") != candidate["user"].get("login")
        or pull["user"].get("id") != candidate["user"].get("id")
        or pull.get("html_url") != candidate.get("html_url")
        or not isinstance(head, dict)
        or re.fullmatch(r"[0-9a-f]{40}", str(head.get("sha") or "")) is None
        or not isinstance(author, dict)
        or not isinstance(author.get("login"), str)
        or not author["login"]
        or type(author.get("id")) is not int
        or author["id"] <= 0
        or not isinstance(merger, dict)
        or not isinstance(merger.get("login"), str)
        or not merger["login"]
        or type(merger.get("id")) is not int
        or merger["id"] <= 0
        or html_url != f"https://github.com/coralehr/fhir-mcp-eval/pull/{number}"
    ):
        raise ValueError("external anchor pull-request metadata is malformed")

    files: list[Any] = []
    for page in range(1, 11):
        page_files = _load_json_value(
            fetch_bytes(
                _github_api_url(
                    f"/pulls/{number}/files?per_page=100&page={page}"
                ),
                "application/vnd.github+json",
            ),
            label="external anchor pull-request file metadata",
        )
        if not isinstance(page_files, list):
            raise ValueError("external anchor pull-request file metadata is malformed")
        files.extend(page_files)
        if len(page_files) < 100:
            break
    else:
        raise ValueError("external anchor pull-request file metadata exceeds page cap")
    matching_files = [
        file
        for file in files
        if isinstance(file, dict)
        and file.get("filename") == locator.path
        and file.get("status") in {"added", "modified"}
    ]
    if len(matching_files) != 1:
        raise ValueError(
            "external anchor path was not added or modified by the approved PR"
        )
    reviewed_head = fetch_bytes(
        _github_api_url(f"/contents/{locator.path}?ref={head['sha']}"),
        "application/vnd.github.raw+json",
    )
    if reviewed_head != expected_anchor_bytes:
        raise ValueError("external anchor bytes differ from the reviewed PR head")

    reviews: list[Any] = []
    for page in range(1, 11):
        page_reviews = _load_json_value(
            fetch_bytes(
                _github_api_url(
                    f"/pulls/{number}/reviews?per_page=100&page={page}"
                ),
                "application/vnd.github+json",
            ),
            label="external anchor review metadata",
        )
        if not isinstance(page_reviews, list):
            raise ValueError("external anchor review metadata is malformed")
        reviews.extend(page_reviews)
        if len(page_reviews) < 100:
            break
    else:
        raise ValueError("external anchor review metadata exceeds page cap")
    latest: dict[int, tuple[int, dict[str, Any]]] = {}
    for review in reviews:
        user = review.get("user") if isinstance(review, dict) else None
        login = user.get("login") if isinstance(user, dict) else None
        user_id = user.get("id") if isinstance(user, dict) else None
        review_id = review.get("id") if isinstance(review, dict) else None
        if (
            isinstance(login, str)
            and login
            and type(user_id) is int
            and user_id > 0
            and type(review_id) is int
            and review_id > 0
            and review.get("commit_id") == head["sha"]
            and user.get("type") == "User"
        ):
            previous = latest.get(user_id)
            if previous is None or review_id > previous[0]:
                latest[user_id] = (review_id, review)
    disallowed_ids = {author["id"], merger["id"]}
    disallowed_logins = {author["login"].casefold(), merger["login"].casefold()}
    approved = sorted(
        (
            user_id,
            review["user"]["login"],
        )
        for user_id, (_review_id, review) in latest.items()
        if user_id not in disallowed_ids
        and review["user"]["login"].casefold() not in disallowed_logins
        and user_id in TRUSTED_INDEPENDENT_APPROVERS_BY_ID
        and review["user"]["login"].casefold()
        == TRUSTED_INDEPENDENT_APPROVERS_BY_ID[user_id].casefold()
        and review.get("state") == "APPROVED"
        and review.get("author_association") in TRUSTED_REVIEWER_ASSOCIATIONS
        and isinstance(review.get("submitted_at"), str)
        and review["submitted_at"]
        and review["submitted_at"] <= pull["merged_at"]
    )
    if not approved:
        raise ValueError(
            "external anchor has no independent approval on the exact PR head"
        )
    return {
        "anchor_pr_number": number,
        "anchor_pr_url": html_url,
        "anchor_pr_head_sha": head["sha"],
        "anchor_path": locator.path,
        "anchor_pr_file_status": matching_files[0]["status"],
        "anchor_pr_head_file_sha256": sha256_bytes(reviewed_head),
        "anchor_pr_author": author["login"],
        "anchor_pr_author_id": author["id"],
        "anchor_pr_merged_by": merger["login"],
        "anchor_pr_merged_by_id": merger["id"],
        "independent_approvers": [login for _user_id, login in approved],
        "independent_approver_ids": [user_id for user_id, _login in approved],
    }


def verify_external_anchor(
    controller_manifest: Path,
    anchor_url: str,
    *,
    expected_controller_sha256: str,
    fetch_bytes: Callable[[str, str], bytes] = fetch_github_bytes,
) -> dict[str, Any]:
    """Verify exact request bytes in an independently approved signed commit."""

    locator = parse_github_anchor_url(anchor_url)
    request = build_anchor_request(controller_manifest)
    if request["controller"]["sha256"] != expected_controller_sha256:
        raise ValueError("external anchor does not bind the expected controller")
    expected = canonical_json_bytes(request)
    published = fetch_bytes(
        locator.contents_url, "application/vnd.github.raw+json"
    )
    if published != expected:
        raise ValueError(
            "external anchor request bytes differ from the controller seal"
        )

    commit = _load_json_object(
        fetch_bytes(locator.commit_url, "application/vnd.github+json"),
        label="GitHub commit verification",
    )
    commit_details = commit.get("commit")
    verification = (
        commit_details.get("verification")
        if isinstance(commit_details, dict)
        else None
    )
    if commit.get("sha") != locator.commit_sha:
        raise ValueError("external anchor commit differs from the pinned commit")
    if (
        not isinstance(verification, dict)
        or verification.get("verified") is not True
        or verification.get("reason") != "valid"
        or not isinstance(verification.get("verified_at"), str)
        or not verification["verified_at"]
    ):
        raise ValueError("external anchor commit signature is not verified")

    approval = _independent_pr_approval(
        locator,
        expected_anchor_bytes=expected,
        fetch_bytes=fetch_bytes,
    )
    return {
        "kind": "experiment_external_anchor_verification",
        "schema_version": ANCHOR_VERIFICATION_VERSION,
        "controller_manifest_sha256": request["controller"]["sha256"],
        "anchor_request_sha256": sha256_bytes(expected),
        "anchor_url": anchor_url,
        "external_commit_sha": locator.commit_sha,
        "github_signature_verified": True,
        "github_signature_reason": verification["reason"],
        "github_signature_verified_at": verification["verified_at"],
        **approval,
    }


_VERIFICATION_RECEIPT_FIELDS = frozenset(
    {
        "kind",
        "schema_version",
        "controller_manifest_sha256",
        "anchor_request_sha256",
        "anchor_url",
        "external_commit_sha",
        "github_signature_verified",
        "github_signature_reason",
        "github_signature_verified_at",
        "anchor_pr_number",
        "anchor_pr_url",
        "anchor_pr_head_sha",
        "anchor_path",
        "anchor_pr_file_status",
        "anchor_pr_head_file_sha256",
        "anchor_pr_author",
        "anchor_pr_author_id",
        "anchor_pr_merged_by",
        "anchor_pr_merged_by_id",
        "independent_approvers",
        "independent_approver_ids",
    }
)


def _validate_receipt_contract(
    receipt: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    expected_request: bytes,
    anchor_url: str,
    expected_controller_sha256: str,
    locator: GitHubAnchorLocator,
) -> None:
    if set(receipt) != _VERIFICATION_RECEIPT_FIELDS:
        raise ValueError("external anchor verification receipt changed: schema")
    if (
        receipt.get("kind") != "experiment_external_anchor_verification"
        or receipt.get("schema_version") != ANCHOR_VERIFICATION_VERSION
    ):
        raise ValueError("external anchor verification receipt changed: identity")
    if (
        request["controller"]["sha256"] != expected_controller_sha256
        or receipt.get("controller_manifest_sha256") != expected_controller_sha256
        or receipt.get("anchor_request_sha256") != sha256_bytes(expected_request)
        or receipt.get("anchor_url") != anchor_url
        or receipt.get("external_commit_sha") != locator.commit_sha
    ):
        raise ValueError("external anchor verification receipt changed: binding")
    if (
        receipt.get("github_signature_verified") is not True
        or receipt.get("github_signature_reason") != "valid"
        or not isinstance(receipt.get("github_signature_verified_at"), str)
        or not receipt["github_signature_verified_at"]
    ):
        raise ValueError("external anchor verification receipt changed: signature")
    if (
        receipt.get("anchor_path") != locator.path
        or receipt.get("anchor_pr_file_status") not in {"added", "modified"}
        or receipt.get("anchor_pr_head_file_sha256")
        != sha256_bytes(expected_request)
    ):
        raise ValueError("external anchor verification receipt changed: reviewed file")

    number = receipt.get("anchor_pr_number")
    head = receipt.get("anchor_pr_head_sha")
    author = receipt.get("anchor_pr_author")
    author_id = receipt.get("anchor_pr_author_id")
    merger = receipt.get("anchor_pr_merged_by")
    merger_id = receipt.get("anchor_pr_merged_by_id")
    approvers = receipt.get("independent_approvers")
    approver_ids = receipt.get("independent_approver_ids")
    if (
        type(number) is not int
        or number <= 0
        or receipt.get("anchor_pr_url")
        != f"https://github.com/coralehr/fhir-mcp-eval/pull/{number}"
        or re.fullmatch(r"[0-9a-f]{40}", str(head or "")) is None
        or not isinstance(author, str)
        or not author
        or type(author_id) is not int
        or author_id <= 0
        or not isinstance(merger, str)
        or not merger
        or type(merger_id) is not int
        or merger_id <= 0
        or not isinstance(approvers, list)
        or not approvers
        or not isinstance(approver_ids, list)
        or not approver_ids
        or len(approvers) != len(approver_ids)
        or any(
            not isinstance(login, str)
            or not login
            or login.casefold() in {author.casefold(), merger.casefold()}
            for login in approvers
        )
        or any(
            type(user_id) is not int
            or user_id <= 0
            or user_id in {author_id, merger_id}
            or user_id not in TRUSTED_INDEPENDENT_APPROVERS_BY_ID
            or TRUSTED_INDEPENDENT_APPROVERS_BY_ID[user_id].casefold()
            != login.casefold()
            for user_id, login in zip(approver_ids, approvers, strict=True)
        )
        or len({login.casefold() for login in approvers}) != len(approvers)
        or len(set(approver_ids)) != len(approver_ids)
        or list(zip(approver_ids, approvers, strict=True))
        != sorted(zip(approver_ids, approvers, strict=True))
    ):
        raise ValueError("external anchor verification receipt changed: approval")


def _read_existing_receipt(
    *,
    controller_manifest: Path,
    anchor_url: str,
    expected_controller_sha256: str,
    receipt_path: Path,
) -> dict[str, Any] | None:
    sidecar = receipt_path.with_suffix(".sha256")
    if not receipt_path.exists() and not sidecar.exists():
        return None
    request = build_anchor_request(controller_manifest)
    expected_request = canonical_json_bytes(request)
    locator = parse_github_anchor_url(anchor_url)
    try:
        payload = receipt_path.read_bytes()
        sidecar_payload = sidecar.read_bytes()
        receipt = _load_json_object(payload, label="external anchor receipt")
        writable = (
            stat.S_IMODE(receipt_path.stat().st_mode) & 0o222
            or stat.S_IMODE(sidecar.stat().st_mode) & 0o222
        )
    except OSError as exc:
        raise ValueError("external anchor verification receipt changed") from exc
    if receipt_path.is_symlink() or sidecar.is_symlink() or writable:
        raise ValueError("external anchor verification receipt changed: permissions")
    if sidecar_payload != (sha256_bytes(payload) + "\n").encode("ascii"):
        raise ValueError("external anchor verification receipt changed: sidecar")
    if payload != canonical_json_bytes(receipt):
        raise ValueError("external anchor verification receipt changed: noncanonical")
    _validate_receipt_contract(
        receipt,
        request=request,
        expected_request=expected_request,
        anchor_url=anchor_url,
        expected_controller_sha256=expected_controller_sha256,
        locator=locator,
    )
    return receipt


def verify_recorded_external_anchor(
    controller_manifest: Path,
    anchor_url: str,
    receipt_path: Path,
    *,
    expected_controller_sha256: str,
) -> dict[str, Any]:
    """Validate one previously recorded immutable external-anchor receipt."""

    receipt = _read_existing_receipt(
        controller_manifest=controller_manifest,
        anchor_url=anchor_url,
        expected_controller_sha256=expected_controller_sha256,
        receipt_path=receipt_path.absolute(),
    )
    if receipt is None:
        raise ValueError("external anchor verification receipt is missing")
    return receipt


def verify_and_record_external_anchor(
    controller_manifest: Path,
    anchor_url: str,
    receipt_path: Path,
    *,
    expected_controller_sha256: str,
    fetch_bytes: Callable[[str, str], bytes] = fetch_github_bytes,
) -> dict[str, Any]:
    """Verify the external anchor and persist one exact resume-safe receipt."""

    receipt_path = receipt_path.absolute()
    sidecar = receipt_path.with_suffix(".sha256")
    receipt_present = receipt_path.exists() or receipt_path.is_symlink()
    sidecar_present = sidecar.exists() or sidecar.is_symlink()
    receipt = verify_external_anchor(
        controller_manifest,
        anchor_url,
        expected_controller_sha256=expected_controller_sha256,
        fetch_bytes=fetch_bytes,
    )
    payload = canonical_json_bytes(receipt)
    digest = sha256_bytes(payload)
    sidecar_payload = (digest + "\n").encode("ascii")

    if receipt_present and sidecar_present:
        existing = _read_existing_receipt(
            controller_manifest=controller_manifest,
            anchor_url=anchor_url,
            expected_controller_sha256=expected_controller_sha256,
            receipt_path=receipt_path,
        )
        if existing != receipt:
            raise ValueError("external anchor verification receipt changed")
        return receipt

    if receipt_present:
        if (
            receipt_path.is_symlink()
            or receipt_path.read_bytes() != payload
            or stat.S_IMODE(receipt_path.stat().st_mode) & 0o222
        ):
            raise ValueError("external anchor verification receipt changed")
    if sidecar_present:
        if (
            sidecar.is_symlink()
            or sidecar.read_bytes() != sidecar_payload
            or stat.S_IMODE(sidecar.stat().st_mode) & 0o222
        ):
            raise ValueError("external anchor verification receipt changed")

    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    created_receipt = False
    created_sidecar = False
    try:
        if not receipt_present:
            with receipt_path.open("xb") as handle:
                created_receipt = True
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            receipt_path.chmod(0o444)
        if not sidecar_present:
            with sidecar.open("xb") as handle:
                created_sidecar = True
                handle.write(sidecar_payload)
                handle.flush()
                os.fsync(handle.fileno())
            sidecar.chmod(0o444)
    except BaseException:
        if created_receipt:
            receipt_path.unlink(missing_ok=True)
        if created_sidecar:
            sidecar.unlink(missing_ok=True)
        raise
    return receipt


def sign_external_anchor_verification(
    receipt: Mapping[str, Any],
    *,
    private_key_path: Path,
    verifier: Mapping[str, Any],
) -> dict[str, Any]:
    """Sign a live GitHub verification result on the independent checker host."""

    verifier_value = _anchor_verifier(verifier)
    private_key_path = private_key_path.resolve()
    if not private_key_path.is_file():
        raise ValueError("anchor checker private key is unavailable")
    try:
        derived = subprocess.run(
            [str(SSH_KEYGEN_PATH), "-y", "-f", str(private_key_path)],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=SSH_KEYGEN_ENV,
            timeout=SSH_KEYGEN_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("anchor checker public key could not be derived") from exc
    derived_fields = derived.stdout.strip().split()
    if (
        derived.returncode != 0
        or len(derived_fields) < 2
        or " ".join(derived_fields[:2]) != verifier_value["public_key"]
    ):
        raise ValueError("anchor checker public key differs from the trust pin")

    body = {**dict(receipt), "anchor_verifier_key_id": verifier_value["key_id"]}
    body_bytes = canonical_json_bytes(body)
    signed_payload = SIGNED_ANCHOR_DOMAIN + body_bytes
    try:
        with tempfile.TemporaryDirectory(
            prefix="experiment-anchor-sign-"
        ) as directory:
            message = Path(directory) / "verification"
            message.write_bytes(signed_payload)
            process = subprocess.run(
                [
                    str(SSH_KEYGEN_PATH),
                    "-Y",
                    "sign",
                    "-q",
                    "-f",
                    str(private_key_path),
                    "-n",
                    SIGNED_ANCHOR_NAMESPACE,
                    str(message),
                ],
                check=False,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                env=SSH_KEYGEN_ENV,
                timeout=SSH_KEYGEN_TIMEOUT_SECONDS,
            )
            signature_path = message.with_name(message.name + ".sig")
            if process.returncode != 0 or not signature_path.is_file():
                raise ValueError("anchor verification receipt signing failed")
            signature_value = base64.b64encode(signature_path.read_bytes()).decode(
                "ascii"
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("anchor verification receipt signing failed") from exc
    return {
        "kind": SIGNED_ANCHOR_VERIFICATION_KIND,
        "schema_version": SIGNED_ANCHOR_VERIFICATION_VERSION,
        "body": body,
        "body_sha256": sha256_bytes(body_bytes),
        "signature": {
            "algorithm": "ssh-ed25519",
            "identity": verifier_value["identity"],
            "namespace": SIGNED_ANCHOR_NAMESPACE,
            "key_id": verifier_value["key_id"],
            "value_base64": signature_value,
        },
    }


def verify_signed_external_anchor_receipt(
    controller_manifest: Path,
    anchor_url: str,
    receipt_bytes: bytes,
    *,
    expected_controller_sha256: str,
    expected_verifier: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify a checker-signed, offline-safe external approval receipt."""

    verifier = _anchor_verifier(expected_verifier)
    envelope = _load_json_object(receipt_bytes, label="signed anchor receipt")
    if receipt_bytes != canonical_json_bytes(envelope):
        raise ValueError("signed anchor verification receipt is noncanonical")
    if set(envelope) != {
        "kind",
        "schema_version",
        "body",
        "body_sha256",
        "signature",
    } or (
        envelope.get("kind") != SIGNED_ANCHOR_VERIFICATION_KIND
        or envelope.get("schema_version") != SIGNED_ANCHOR_VERIFICATION_VERSION
    ):
        raise ValueError("signed anchor verification receipt schema changed")
    body = envelope.get("body")
    if not isinstance(body, Mapping) or set(body) != {
        *_VERIFICATION_RECEIPT_FIELDS,
        "anchor_verifier_key_id",
    }:
        raise ValueError("signed anchor verification receipt body changed")
    body_bytes = canonical_json_bytes(body)
    if (
        body.get("anchor_verifier_key_id") != verifier["key_id"]
        or envelope.get("body_sha256") != sha256_bytes(body_bytes)
    ):
        raise ValueError("signed anchor verification receipt binding changed")

    request = build_anchor_request(controller_manifest)
    expected_request = canonical_json_bytes(request)
    locator = parse_github_anchor_url(anchor_url)
    unsigned_body = {
        key: body[key] for key in _VERIFICATION_RECEIPT_FIELDS
    }
    _validate_receipt_contract(
        unsigned_body,
        request=request,
        expected_request=expected_request,
        anchor_url=anchor_url,
        expected_controller_sha256=expected_controller_sha256,
        locator=locator,
    )

    signature = envelope.get("signature")
    if not isinstance(signature, Mapping) or set(signature) != {
        "algorithm",
        "identity",
        "namespace",
        "key_id",
        "value_base64",
    } or signature != {
        "algorithm": "ssh-ed25519",
        "identity": verifier["identity"],
        "namespace": SIGNED_ANCHOR_NAMESPACE,
        "key_id": verifier["key_id"],
        "value_base64": signature.get("value_base64"),
    }:
        raise ValueError("signed anchor verification receipt signature changed")
    try:
        signature_bytes = base64.b64decode(
            signature["value_base64"], validate=True
        )
        with tempfile.TemporaryDirectory(
            prefix="experiment-anchor-verify-"
        ) as directory:
            root = Path(directory)
            allowed_signers = root / "allowed_signers"
            allowed_signers.write_text(
                f'{verifier["identity"]} {verifier["public_key"]}\n',
                encoding="ascii",
            )
            signature_path = root / "verification.sig"
            signature_path.write_bytes(signature_bytes)
            process = subprocess.run(
                [
                    str(SSH_KEYGEN_PATH),
                    "-Y",
                    "verify",
                    "-q",
                    "-f",
                    str(allowed_signers),
                    "-I",
                    verifier["identity"],
                    "-n",
                    SIGNED_ANCHOR_NAMESPACE,
                    "-s",
                    str(signature_path),
                ],
                input=SIGNED_ANCHOR_DOMAIN + body_bytes,
                check=False,
                capture_output=True,
                env=SSH_KEYGEN_ENV,
                timeout=SSH_KEYGEN_TIMEOUT_SECONDS,
            )
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        raise ValueError("signed anchor verification receipt is invalid") from exc
    if process.returncode != 0:
        raise ValueError("signed anchor verification receipt is invalid")
    return unsigned_body


def build_anchor_request(controller_manifest: Path) -> dict[str, Any]:
    """Return the canonical public digest inventory for one sealed controller."""

    if controller_manifest.is_symlink() or not controller_manifest.is_file():
        raise ValueError("controller manifest must be a regular file")
    controller_manifest = controller_manifest.resolve()
    controller_bytes = controller_manifest.read_bytes()
    manifest = _load_json_object(controller_bytes, label="controller manifest")
    controller_version = manifest.get("schema_version")
    if controller_version not in {"a11-controller-v3", "a11-controller-v4"}:
        raise ValueError("external anchors require an A11 v3 or v4 controller")

    snapshots = manifest.get("snapshots")
    execution = manifest.get("execution")
    grading = manifest.get("grading")
    if not isinstance(snapshots, dict) or not isinstance(execution, dict):
        raise ValueError("controller anchor inputs are incomplete")
    codex = execution.get("codex")
    native = codex.get("native") if isinstance(codex, dict) else None
    panel = grading.get("panel") if isinstance(grading, dict) else None
    if not isinstance(panel, dict):
        raise ValueError("controller panel configuration is missing")

    subjects = {
        public_name: _receipt(snapshots.get(snapshot_name), label=public_name)
        for public_name, snapshot_name in _SNAPSHOT_SUBJECTS.items()
    }
    if manifest.get("experiment_profile") == "a11b-causal-isolation-v2":
        subjects["a11b_postprocess"] = _receipt(
            snapshots.get("a11b_postprocess"), label="a11b_postprocess"
        )
        subjects["answer_input"] = _receipt(
            snapshots.get("answer_input"), label="answer_input"
        )
        subjects["a11b_nightly_bootstrap"] = _receipt(
            snapshots.get("a11b_nightly_bootstrap"),
            label="a11b_nightly_bootstrap",
        )
        subjects["a11b_nightly_runner"] = _receipt(
            snapshots.get("a11b_nightly_runner"), label="a11b_nightly_runner"
        )
        inputs = manifest.get("inputs")
        if isinstance(inputs, Mapping) and (
            "python_tree_receipt_sha256" in inputs
            or "install_manifest_sha256" in inputs
        ):
            python_tree = _receipt(
                snapshots.get("python_tree"), label="python_tree"
            )
            install_manifest = _receipt(
                snapshots.get("install_manifest"), label="install_manifest"
            )
            if (
                inputs.get("python_tree_receipt_sha256")
                != python_tree["sha256"]
                or inputs.get("install_manifest_sha256")
                != install_manifest["sha256"]
            ):
                raise ValueError("controller install snapshot binding changed")
            subjects["python_tree"] = python_tree
            subjects["install_manifest"] = install_manifest
    subjects["native_codex"] = _receipt(native, label="native_codex")

    model_configuration = _model_configuration(
        {
            "answer": {
                key: execution.get(key)
                for key in ("model", "reasoning_effort", "timeout_seconds")
            },
            "panel": {
                key: panel.get(key)
                for key in (
                    "model",
                    "reasoning_effort",
                    "votes",
                    "batch_size",
                    "timeout_seconds",
                )
            },
        }
    )
    request = {
        "kind": ANCHOR_REQUEST_KIND,
        "schema_version": (
            ANCHOR_REQUEST_VERSION_V2
            if controller_version == "a11-controller-v4"
            else ANCHOR_REQUEST_VERSION
        ),
        "experiment_profile": manifest.get("experiment_profile"),
        "controller": {
            "kind": manifest.get("kind"),
            "schema_version": manifest.get("schema_version"),
            "sha256": sha256_bytes(controller_bytes),
            "bytes": len(controller_bytes),
        },
        "subjects": subjects,
        "model_configuration": model_configuration,
    }
    if controller_version == "a11-controller-v4":
        profile = manifest.get("experiment_profile")
        expected_code_names = (
            (
                "a11b_launch_protocol",
                "a11b_nightly_bootstrap",
                "a11b_nightly_runner",
                "anchor",
                "bootstrap",
                "codex_harness",
                "driver",
                "executor",
                "service",
                "witness",
            )
            if profile == "a11b-causal-isolation-v2"
            else (
                "anchor",
                "bootstrap",
                "codex_harness",
                "driver",
                "executor",
                "service",
                "witness",
            )
        )
        trusted_executor = _trusted_executor_binding(
            execution.get("trusted_executor"),
            expected_code_names=expected_code_names,
        )
        if trusted_executor["model_configuration"] != model_configuration:
            raise ValueError(
                "controller model configuration differs from trusted executor"
            )
        if {
            key: native.get(key) for key in ("path", "sha256", "bytes")
        } != {
            key: trusted_executor["runtime"][key]
            for key in ("path", "sha256", "bytes")
        }:
            raise ValueError("controller native Codex differs from trusted executor")
        request["trusted_executor"] = trusted_executor
    elif "trusted_executor" in execution:
        raise ValueError("A11 v3 cannot carry an unanchored trusted executor")
    return request


def write_anchor_request(
    controller_manifest: Path, output_path: Path
) -> dict[str, object]:
    """Write the exact bytes that a separate host must publish."""

    output_path = output_path.absolute()
    sidecar = output_path.with_suffix(".sha256")
    if (
        output_path.exists()
        or output_path.is_symlink()
        or sidecar.exists()
        or sidecar.is_symlink()
    ):
        raise FileExistsError("external anchor request already exists")
    payload = canonical_json_bytes(build_anchor_request(controller_manifest))
    digest = sha256_bytes(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    created_output = False
    created_sidecar = False
    try:
        with output_path.open("xb") as handle:
            created_output = True
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        output_path.chmod(0o444)
        with sidecar.open("xb") as handle:
            created_sidecar = True
            handle.write((digest + "\n").encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
        sidecar.chmod(0o444)
    except BaseException:
        if created_output:
            output_path.unlink(missing_ok=True)
        if created_sidecar:
            sidecar.unlink(missing_ok=True)
        raise
    return {
        "path": str(output_path.resolve()),
        "sha256": digest,
        "bytes": len(payload),
    }


def verify_local_anchor_request(
    controller_manifest: Path, request_path: Path
) -> None:
    """Verify the immutable local copy prepared for external publication."""

    request_path = request_path.absolute()
    sidecar = request_path.with_suffix(".sha256")
    expected = canonical_json_bytes(build_anchor_request(controller_manifest))
    digest = sha256_bytes(expected)
    try:
        request_bytes = request_path.read_bytes()
        sidecar_bytes = sidecar.read_bytes()
        writable = (
            stat.S_IMODE(request_path.stat().st_mode) & 0o222
            or stat.S_IMODE(sidecar.stat().st_mode) & 0o222
        )
    except OSError as exc:
        raise ValueError("local anchor request changed or is missing") from exc
    if (
        request_path.is_symlink()
        or sidecar.is_symlink()
        or writable
        or request_bytes != expected
        or sidecar_bytes != (digest + "\n").encode("ascii")
    ):
        raise ValueError("local anchor request changed or is missing")
    return {
        "path": str(request_path.resolve()),
        "sha256": digest,
        "bytes": len(expected),
    }
