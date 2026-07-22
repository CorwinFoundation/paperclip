#!/usr/bin/env python3
"""Validate deployable scanner-edge release identity.

BEAAA-16095 keeps the production verifier strict: a deployable image signature
must come from the reviewed scanner-edge-image workflow at refs/heads/master,
while the OCI revision and release record bind the built source commit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


EXPECTED_SUBJECT = (
    "https://github.com/CorwinFoundation/are-scanner/"
    ".github/workflows/scanner-edge-image.yml@refs/heads/master"
)
EXPECTED_ISSUER = "https://token.actions.githubusercontent.com"
EXPECTED_WORKFLOW_REF = (
    "CorwinFoundation/are-scanner/"
    ".github/workflows/scanner-edge-image.yml@refs/heads/master"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ReleaseIdentityError(ValueError):
    """Raised when release identity is not deployable."""


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _cosign_entry(cosign_verify: Any) -> dict[str, Any]:
    if isinstance(cosign_verify, list) and cosign_verify:
        entry = cosign_verify[0]
    elif isinstance(cosign_verify, dict):
        entry = cosign_verify
    else:
        raise ReleaseIdentityError("cosign verification output is empty")
    if not isinstance(entry, dict):
        raise ReleaseIdentityError("cosign verification output entry is not an object")
    return entry


def _optional(entry: dict[str, Any]) -> dict[str, Any]:
    value = entry.get("optional") or entry.get("Optional") or {}
    return value if isinstance(value, dict) else {}


def _critical(entry: dict[str, Any]) -> dict[str, Any]:
    value = entry.get("critical") or entry.get("Critical") or {}
    return value if isinstance(value, dict) else {}


def _signed_digest(entry: dict[str, Any]) -> str | None:
    image = _critical(entry).get("image") or {}
    if isinstance(image, dict):
        return image.get("docker-manifest-digest") or image.get("Docker-manifest-digest")
    return None


def _release_digest(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if "@sha256:" in value:
        return value.rsplit("@", 1)[1]
    return value


def validate_release_identity(
    release_candidate: dict[str, Any],
    cosign_verify: Any,
    *,
    expected_source_sha: str | None = None,
    expected_image_digest: str | None = None,
) -> dict[str, str]:
    """Return normalized identity fields or raise on a non-deployable release."""

    entry = _cosign_entry(cosign_verify)
    opt = _optional(entry)

    subject = opt.get("Subject") or opt.get("subject")
    issuer = opt.get("Issuer") or opt.get("issuer")
    workflow_ref = opt.get("githubWorkflowRef")
    workflow_sha = opt.get("githubWorkflowSha")
    signed_digest = _signed_digest(entry)

    source_sha = release_candidate.get("sourceCommitSha")
    commit_sha = release_candidate.get("commitSha")
    oci_revision = release_candidate.get("ociRevision")
    image_ref_or_digest = release_candidate.get("imageDigest")
    image_digest = _release_digest(image_ref_or_digest)
    candidate_workflow_ref = release_candidate.get("workflowRef")
    origin_master_tip = release_candidate.get("originMasterTip")
    release_subject = release_candidate.get("signatureSubject")
    release_issuer = release_candidate.get("signatureOidcIssuer")
    release_workflow_ref = release_candidate.get("signatureWorkflowRef")

    errors: list[str] = []
    if subject is not None and subject != EXPECTED_SUBJECT:
        errors.append(f"signature Subject={subject!r} expected {EXPECTED_SUBJECT!r}")
    if issuer is not None and issuer != EXPECTED_ISSUER:
        errors.append(f"signature Issuer={issuer!r} expected {EXPECTED_ISSUER!r}")
    if workflow_ref is not None and workflow_ref != EXPECTED_WORKFLOW_REF:
        errors.append(f"githubWorkflowRef={workflow_ref!r} expected {EXPECTED_WORKFLOW_REF!r}")
    if candidate_workflow_ref != EXPECTED_WORKFLOW_REF:
        errors.append(f"workflowRef={candidate_workflow_ref!r} expected {EXPECTED_WORKFLOW_REF!r}")
    if release_subject != EXPECTED_SUBJECT:
        errors.append(f"release signatureSubject={release_subject!r} expected {EXPECTED_SUBJECT!r}")
    if release_issuer != EXPECTED_ISSUER:
        errors.append(f"release signatureOidcIssuer={release_issuer!r} expected {EXPECTED_ISSUER!r}")
    if release_workflow_ref != EXPECTED_WORKFLOW_REF:
        errors.append(
            f"release signatureWorkflowRef={release_workflow_ref!r} expected {EXPECTED_WORKFLOW_REF!r}"
        )

    if not isinstance(source_sha, str) or not SHA_RE.fullmatch(source_sha):
        errors.append("sourceCommitSha must be an explicit full lowercase SHA")
    if commit_sha != source_sha:
        errors.append(f"commitSha={commit_sha!r} does not match sourceCommitSha={source_sha!r}")
    if oci_revision != source_sha:
        errors.append(f"ociRevision={oci_revision!r} does not match sourceCommitSha={source_sha!r}")
    if origin_master_tip != source_sha:
        errors.append(f"originMasterTip={origin_master_tip!r} does not match sourceCommitSha={source_sha!r}")
    if expected_source_sha is not None and source_sha != expected_source_sha:
        errors.append(f"sourceCommitSha={source_sha!r} expected {expected_source_sha!r}")

    if not isinstance(image_digest, str) or not image_digest.startswith("sha256:"):
        errors.append("imageDigest must be an explicit sha256 digest or image ref pinned by sha256")
    if signed_digest is not None and signed_digest != image_digest:
        errors.append(
            f"signed image digest={signed_digest!r} does not match release imageDigest={image_digest!r}"
        )
    if expected_image_digest is not None and image_ref_or_digest != expected_image_digest:
        errors.append(f"imageDigest={image_ref_or_digest!r} expected {expected_image_digest!r}")

    expected_workflow_sha = release_candidate.get("workflowCommitSha")
    if workflow_sha is not None and expected_workflow_sha is not None and workflow_sha != expected_workflow_sha:
        errors.append(
            f"githubWorkflowSha={workflow_sha!r} does not match workflowCommitSha={expected_workflow_sha!r}"
        )

    if errors:
        raise ReleaseIdentityError("; ".join(errors))

    return {
        "sourceCommitSha": source_sha,
        "imageDigest": image_ref_or_digest,
        "signatureSubject": release_subject,
        "signatureWorkflowRef": release_workflow_ref,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-candidate", required=True, type=Path)
    parser.add_argument("--cosign-verify", required=True, type=Path)
    parser.add_argument("--expected-source-sha")
    parser.add_argument("--expected-image-digest")
    args = parser.parse_args(argv)

    try:
        report = validate_release_identity(
            _load_json(args.release_candidate),
            _load_json(args.cosign_verify),
            expected_source_sha=args.expected_source_sha,
            expected_image_digest=args.expected_image_digest,
        )
    except ReleaseIdentityError as exc:
        print(f"release identity gate FAILED: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"status": "pass", **report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
