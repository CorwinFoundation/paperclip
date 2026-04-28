"""Forensic bundle signing stub (Task B).

v0.1 = HMAC-SHA256 detached signature over canonical-JSON manifest.
Real impl swaps this for an asymmetric scheme + durable store; the call surface
(`build`, `verify`, `get`) is what downstream API + claims forensic spine binds to.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from typing import Any

# Deterministic namespace UUID for bundle ids (uuid5 over canonical request).
_BUNDLE_NS = uuid.UUID("c1c1f1c0-bea1-4bea-b001-bea1bea1bea1")
_DEFAULT_KEY = b"v0.1-dev-key-do-not-use-in-prod"


class BundleError(ValueError):
    pass


def _signing_key() -> bytes:
    raw = os.environ.get("BMI_BUNDLE_SIGNING_KEY")
    return raw.encode("utf-8") if raw else _DEFAULT_KEY


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def derive_bundle_id(snapshot_hash: str, tenant_id: str, scored_at: str) -> str:
    """Deterministic bundle_id so same inputs → same id (red-line determinism)."""
    seed = f"{tenant_id}|{snapshot_hash}|{scored_at}"
    return str(uuid.uuid5(_BUNDLE_NS, seed))


def _sign(manifest: dict[str, Any]) -> str:
    mac = hmac.new(_signing_key(), _canonical(manifest), hashlib.sha256)
    return "hmac-sha256:" + mac.hexdigest()


class BundleStore:
    """In-memory bundle store for v0.1. Durable backend swap is a contract-only change."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    def build(
        self,
        *,
        bundle_id: str,
        snapshot_hash: str,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        manifest = {
            "bundle_id": bundle_id,
            "snapshot_hash": snapshot_hash,
            "evidence": evidence,
        }
        bundle = {
            **manifest,
            "signed_manifest": _sign(manifest),
        }
        self._store[bundle_id] = bundle
        return bundle

    def get(self, bundle_id: str) -> dict[str, Any] | None:
        bundle = self._store.get(bundle_id)
        if bundle is None:
            return None
        # Verify-on-read: tampering detection at retrieval time, not just write.
        verify(bundle)
        return bundle

    def clear(self) -> None:
        self._store.clear()


def verify(bundle: dict[str, Any]) -> None:
    """Raise BundleError if signature does not match manifest contents."""
    expected_sig = bundle.get("signed_manifest")
    if not isinstance(expected_sig, str):
        raise BundleError("missing signed_manifest")
    manifest = {
        "bundle_id": bundle["bundle_id"],
        "snapshot_hash": bundle["snapshot_hash"],
        "evidence": bundle["evidence"],
    }
    actual = _sign(manifest)
    if not hmac.compare_digest(actual, expected_sig):
        raise BundleError("signature mismatch")
