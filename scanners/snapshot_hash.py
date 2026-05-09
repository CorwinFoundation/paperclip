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

Returns "sha256:<64-hex>" matching the api/openapi.yaml snapshot_hash format.
"""
from __future__ import annotations

import hashlib
import json
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
