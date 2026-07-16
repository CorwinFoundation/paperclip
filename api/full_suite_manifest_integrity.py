"""Canonical bytes, hashes, and signatures for full-suite manifests."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from api.full_suite_manifest_models import FullSuiteCase, ManifestSigner, normalize_case


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Return the stable JSON representation used by manifest persistence."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def manifest_payload_hash(manifest: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def registry_snapshot_hash(cases: Iterable[FullSuiteCase]) -> str:
    rows = [normalize_case(case).to_registry_row() for case in cases]
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def attach_manifest_signature(
    manifest: dict[str, Any], signer: ManifestSigner
) -> dict[str, Any]:
    signed = dict(manifest)
    signed["signed_manifest"] = signer(manifest)
    return signed
