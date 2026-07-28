"""Canonical BackBond Scanner release identity loader and drift validator."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CONTRACT_RE = re.compile(r"^are-scanner-api-v1@v([0-9]+\.[0-9]+\.[0-9]+)$")
STABLE_TAG_RE = re.compile(r"^scanner-v([0-9]+\.[0-9]+\.[0-9]+)$")
RC_TAG_RE = re.compile(r"^scanner-v([0-9]+\.[0-9]+\.[0-9]+)-rc\.([1-9][0-9]*)$")
DEPLOY_TAG_RE = re.compile(r"^deploy/([a-z][a-z0-9-]*)/([0-9]{8})/([0-9a-f]{12})$")
REPO_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = REPO_ROOT / "release" / "backbond-scanner-release.json"


class ReleaseManifestError(ValueError):
    """The authored release identity or one of its consumers drifted."""


@dataclass(frozen=True)
class ReleaseIdentity:
    scanner_version: str
    corpus_version: str
    rubric_version: str
    contract_version: str
    api_app_version: str
    model_versions: dict[str, Any]
    build_sha: str | None
    release_tag: str | None


def _read_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReleaseManifestError(f"invalid release manifest JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseManifestError("release manifest must be an object")
    return value, raw


def load_release_identity(path: Path = MANIFEST_PATH) -> ReleaseIdentity:
    value, _ = _read_manifest(path)
    required = ("scanner_version", "corpus_version", "rubric_version", "contract_version", "api_app_version", "model_versions", "build_sha", "release_tag")
    missing = [field for field in required if field not in value]
    if value.get("schema_version") != 2 or missing:
        raise ReleaseManifestError(f"schema_version must be 2; missing fields: {missing}")
    for field in ("scanner_version", "corpus_version", "rubric_version", "api_app_version"):
        if not isinstance(value[field], str) or not SEMVER_RE.fullmatch(value[field]):
            raise ReleaseManifestError(f"{field} must be X.Y.Z")
    if not isinstance(value["contract_version"], str) or not CONTRACT_RE.fullmatch(value["contract_version"]):
        raise ReleaseManifestError("contract_version is invalid")
    model_versions = value["model_versions"]
    if (
        not isinstance(model_versions, dict)
        or not isinstance(model_versions.get("primary"), str)
        or not model_versions["primary"]
        or not isinstance(model_versions.get("judges"), list)
        or not model_versions["judges"]
        or not all(isinstance(judge, str) and judge for judge in model_versions["judges"])
    ):
        raise ReleaseManifestError("model_versions must include primary and non-empty judges")
    if value["build_sha"] is not None and (
        not isinstance(value["build_sha"], str) or not SHA_RE.fullmatch(value["build_sha"])
    ):
        raise ReleaseManifestError("build_sha must be null or a full lowercase SHA")
    if value["release_tag"] is not None and not isinstance(value["release_tag"], str):
        raise ReleaseManifestError("release_tag must be null or a string")
    return ReleaseIdentity(**{field: value[field] for field in required})


def validate_tag(tag: str | None, identity: ReleaseIdentity, build_sha: str) -> None:
    if tag is None:
        return
    stable = STABLE_TAG_RE.fullmatch(tag)
    rc = RC_TAG_RE.fullmatch(tag)
    deploy = DEPLOY_TAG_RE.fullmatch(tag)
    if stable or rc:
        if (stable or rc).group(1) != identity.scanner_version:
            raise ReleaseManifestError("release tag version disagrees with manifest")
        return
    if deploy:
        if deploy.group(3) != build_sha[:12]:
            raise ReleaseManifestError("deploy tag SHA disagrees with build SHA")
        return
    raise ReleaseManifestError("invalid stable, RC, or deploy-candidate tag")


def validate_repository(root: Path, *, build_sha: str, tag: str | None = None) -> ReleaseIdentity:
    if not SHA_RE.fullmatch(build_sha):
        raise ReleaseManifestError("candidate build SHA must be 40 lowercase hex")
    identity = load_release_identity(root / "release" / "backbond-scanner-release.json")
    if identity.build_sha is not None and identity.build_sha != build_sha:
        raise ReleaseManifestError("authored build_sha disagrees with candidate SHA")
    validate_tag(tag if tag is not None else identity.release_tag, identity, build_sha)
    contract_version = CONTRACT_RE.fullmatch(identity.contract_version).group(1)
    if f'version: "v{contract_version}"' not in (root / "contracts" / "are-scanner-api-v1.md").read_text():
        raise ReleaseManifestError("contract reference does not exist")
    if f'version: "{identity.rubric_version}"' not in (root / "rubric" / "rubric.yaml").read_text():
        raise ReleaseManifestError("rubric version disagrees with manifest")
    consumers = {
        "runtime": (root / "scanners" / "__init__.py", "from release_identity import RELEASE_IDENTITY"),
        "api": (root / "api" / "app.py", "RELEASE_IDENTITY.api_app_version"),
        "cli": (root / "bin" / "are-scan", "RELEASE_IDENTITY.scanner_version"),
        "fixture_entrypoint": (
            root / "tools" / "backbond-scanner",
            "RELEASE_IDENTITY.model_versions",
        ),
        "portal": (root / "tools" / "portal_v1" / "portal.py", "from release_identity import RELEASE_IDENTITY"),
        "container": (root / "Dockerfile", "ai.backbond.release.manifest-sha256"),
        "deploy": (
            root / "scripts" / "backbond_deploy_agent.py",
            "release_identity_evidence(REPO_ROOT, build_sha=candidate.commit_sha)",
        ),
    }
    for name, (path, marker) in consumers.items():
        if marker not in path.read_text():
            raise ReleaseManifestError(f"{name} consumer is not bound to release manifest")
    return identity


def release_identity_evidence(root: Path, *, build_sha: str, tag: str | None = None) -> dict[str, Any]:
    identity = validate_repository(root, build_sha=build_sha, tag=tag)
    manifest_path = root / "release" / "backbond-scanner-release.json"
    return {
        "schema_version": 1,
        "scanner_version": identity.scanner_version,
        "corpus_version": identity.corpus_version,
        "rubric_version": identity.rubric_version,
        "contract_version": identity.contract_version,
        "api_app_version": identity.api_app_version,
        "model_versions": identity.model_versions,
        "build_sha": build_sha,
        "release_tag": tag if tag is not None else identity.release_tag,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }


RELEASE_IDENTITY = load_release_identity()
