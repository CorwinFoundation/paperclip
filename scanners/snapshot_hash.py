"""v0.2.3 snapshot_hash canonicalization (BEAAA-21, UW amendment C 2026-05-09).

Canonical recipe for the `snapshot_hash` field on ScoreRequest.snapshot,
extending the v0.2.2 recipe additively for multi-model attestation.

Rules:
  - Single-model scan (model_versions absent or empty): the canonical
    preimage MUST reduce bytes-for-bytes to the v0.2.2 layout — concretely,
    the JSON object {"provider", "provider_model_id", "prompt_version",
    "configs"} serialized with sort_keys=True, separators=(",", ":"), and
    NO model_versions key. This guarantees v0.2.2 ↔ v0.2.3 replay-via-
    snapshot-hash for legacy single-model scans.
  - Multi-model scan (model_versions present and non-empty): the canonical
    preimage adds a "model_versions" key with the same map, also sorted.
    Two multi-model scans differing only in their role→version map MUST
    produce different snapshot_hash values.

BEAAA-539 additive: `compute_corpus_snapshot_hash` extends the same hash
family to cover the probe corpus tarball. Content-addressed: the hash is a
function of file path + size + per-file sha256 over the regular-file
members, sorted by name. Archive-level metadata (mtime, owner, mode, member
ordering, compression format) is excluded by construction so the same
corpus contents produced at different times round-trip to the same hash.

Returns "sha256:<64-hex>" matching the api/openapi.yaml snapshot_hash format.
"""
from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path
from typing import Mapping


def canonicalize_snapshot_hash(
    *,
    provider: str,
    provider_model_id: str,
    prompt_version: str,
    configs: Mapping[str, object] | None = None,
    model_versions: Mapping[str, str] | None = None,
) -> str:
    """Return the canonical snapshot_hash for a snapshot preimage.

    See module docstring for the v0.2.3 vs v0.2.2 byte-equivalence rule.
    """
    preimage: dict[str, object] = {
        "provider": provider,
        "provider_model_id": provider_model_id,
        "prompt_version": prompt_version,
        "configs": dict(configs or {}),
    }
    # v0.2.3 additive: include model_versions ONLY when present and non-empty.
    # Absence MUST yield the v0.2.2 preimage bytes for replay back-compat.
    if model_versions:
        preimage["model_versions"] = dict(model_versions)
    canonical = json.dumps(preimage, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def canonical_preimage_bytes(
    *,
    provider: str,
    provider_model_id: str,
    prompt_version: str,
    configs: Mapping[str, object] | None = None,
    model_versions: Mapping[str, str] | None = None,
) -> bytes:
    """Return the canonical preimage bytes (for byte-equivalence assertions)."""
    preimage: dict[str, object] = {
        "provider": provider,
        "provider_model_id": provider_model_id,
        "prompt_version": prompt_version,
        "configs": dict(configs or {}),
    }
    if model_versions:
        preimage["model_versions"] = dict(model_versions)
    return json.dumps(preimage, sort_keys=True, separators=(",", ":")).encode("utf-8")


# BEAAA-539 additive (rule-execution per scanner-implementation-supervision-
# v1.md@v1.0). Probe corpus snapshot hash for `corpus_snapshot_hash` on the
# T1 scan request (lands in api contract v1.1.0). Same hash family as the
# scan-snapshot recipe above so audit tooling can pin both with one verifier.
_CORPUS_HASH_CHUNK = 65536


def compute_corpus_snapshot_hash(corpus_tarball_path) -> str:
    """Return the canonical `sha256:<64-hex>` digest of a probe corpus tarball.

    The hash is computed over a canonical preimage built from regular-file
    members only:

        for each regular file, sorted by tar member name:
            "<name>\\n<size-in-bytes>\\n<sha256-hex-of-bytes>\\n"

    The concatenation of those lines is hashed with sha256. Archive-level
    metadata (mtime, uid/gid, mode, member ordering on disk, compression
    format) does not appear in the preimage, so two tarballs of the same
    files produced at different times collapse to the same hash. Non-regular
    members (directories, symlinks, hardlinks, devices) are skipped — a
    probe corpus carries only regular files; anything else is a signal the
    tarball was not produced by the corpus packager and raises ValueError.

    Raises FileNotFoundError if the path does not exist.
    """
    path = Path(corpus_tarball_path)
    if not path.exists():
        raise FileNotFoundError(f"corpus tarball not found: {path}")
    digest = hashlib.sha256()
    with tarfile.open(path, mode="r:*") as tar:
        entries: list[tuple[str, int, str]] = []
        for member in tar.getmembers():
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError(
                    f"unexpected non-regular tar member: {member.name!r} "
                    f"(type={member.type!r})"
                )
            extracted = tar.extractfile(member)
            if extracted is None:
                raise ValueError(
                    f"tar member {member.name!r} could not be opened for read"
                )
            member_digest = hashlib.sha256()
            size = 0
            while True:
                chunk = extracted.read(_CORPUS_HASH_CHUNK)
                if not chunk:
                    break
                member_digest.update(chunk)
                size += len(chunk)
            entries.append((member.name, size, member_digest.hexdigest()))
        entries.sort(key=lambda e: e[0])
        for name, size, file_hex in entries:
            digest.update(f"{name}\n{size}\n{file_hex}\n".encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"
