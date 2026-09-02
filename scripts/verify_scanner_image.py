#!/usr/bin/env python3
"""Fail-closed checks for the read-only Scanner image verification lane."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path


SCHEMA_VERSION = "backbond-scanner-readonly-image-verification@2"

# BEAAA-21513 D3. The GHCR image and the Actions artifact are pinned against
# separate expected values and are NOT cross-checked against each other. The
# artifact's `scanner-edge-image.tar` is produced by `scanner-edge-image.yml`,
# which builds and `docker save`s a local image; the GHCR image under test is
# published by `sign-beaaa-19984.yml`. This lane makes no claim that the two
# contain the same image, so the evidence states that limit explicitly rather
# than letting a single `status: PASS` imply a binding it never verifies.
BINDING_MODEL = {
    "image_and_actions_artifact_cross_checked": False,
    "note": (
        "The GHCR image digest and the Actions artifact archive are pinned "
        "independently against separate immutable expected values. The "
        "artifact's scanner-edge-image.tar is not asserted to be the GHCR "
        "image published under the verified digest."
    ),
}


class VerificationError(ValueError):
    """An immutable verification input did not match."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_labels(inspect_path: Path, revision: str, manifest_sha256: str) -> dict[str, str]:
    payload = json.loads(inspect_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != 1:
        raise VerificationError("image inspect payload must contain exactly one image")
    labels = payload[0].get("Config", {}).get("Labels", {}) or {}
    expected = {
        "org.opencontainers.image.revision": revision,
        "ai.backbond.release.manifest-sha256": manifest_sha256,
    }
    for key, value in expected.items():
        if labels.get(key) != value:
            raise VerificationError(f"OCI label mismatch: {key}")
    return expected


def verify_artifact(archive: Path, expected_archive_sha256: str) -> dict[str, str]:
    archive_sha = sha256(archive)
    if archive_sha != expected_archive_sha256:
        raise VerificationError("Actions artifact archive SHA-256 mismatch")

    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        checksum_names = [name for name in names if name.endswith("scanner-edge-image.tar.sha256")]
        tar_names = [name for name in names if name.endswith("scanner-edge-image.tar")]
        if len(checksum_names) != 1 or len(tar_names) != 1:
            raise VerificationError("artifact must contain one image tar and one bundled checksum")
        checksum_text = bundle.read(checksum_names[0]).decode("utf-8").strip()
        match = re.fullmatch(r"([0-9a-f]{64})\s+\*?.*scanner-edge-image\.tar", checksum_text)
        if not match:
            raise VerificationError("bundled image checksum has invalid format")
        tar_sha = hashlib.sha256(bundle.read(tar_names[0])).hexdigest()
        if tar_sha != match.group(1):
            raise VerificationError("bundled image checksum mismatch")
    return {"archive_sha256": archive_sha, "image_tar_sha256": tar_sha}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect-json", type=Path, required=True)
    parser.add_argument("--artifact-zip", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--resolved-digest", required=True)
    parser.add_argument("--expected-digest", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--expected-artifact-sha256", required=True)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--source-artifact", required=True)
    parser.add_argument("--verification-run-url", required=True)
    args = parser.parse_args()

    try:
        if args.resolved_digest != args.expected_digest:
            raise VerificationError("tag resolved to an unexpected digest")
        manifest_sha = sha256(args.source_manifest)
        if manifest_sha != args.expected_manifest_sha256:
            raise VerificationError("frozen source release-manifest SHA-256 mismatch")
        labels = verify_labels(
            args.inspect_json, args.expected_revision, args.expected_manifest_sha256
        )
        artifact = verify_artifact(args.artifact_zip, args.expected_artifact_sha256)
        evidence = {
            "schema_version": SCHEMA_VERSION,
            "status": "PASS",
            "binding_model": BINDING_MODEL,
            "image": {
                "tag_reference": f"{args.repository}:{args.tag}",
                "digest_reference": f"{args.repository}@{args.resolved_digest}",
                "resolved_digest": args.resolved_digest,
                "labels": labels,
                "source_release_manifest_sha256": manifest_sha,
            },
            "actions_artifact": {
                "source_run": args.source_run,
                "source_artifact": args.source_artifact,
                **artifact,
            },
            "verification_run_url": args.verification_run_url,
        }
        args.evidence.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, zipfile.BadZipFile, VerificationError) as exc:
        print(f"scanner image verification FAILED: {exc}", file=sys.stderr)
        return 1
    print("scanner image verification PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
