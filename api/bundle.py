"""Forensic bundle signing stub (Task B); live-probe augmentation (BEAAA-541).

v0.1 = HMAC-SHA256 detached signature over canonical-JSON manifest. The call
surface (`build`, `verify`, `get`) is what the downstream API + claims
forensic spine binds to; a durable asymmetric backend is a contract-only swap.

BEAAA-541 — contract `are-scanner-api-v1@v1.1.0` (op-seat co-sign BEAAA-531,
HoUW countersign BEAAA-535): live-probe bundles additionally carry
`scan_request` (credential-redacted), `prober_version`, and
`target_endpoint_host`. The offline_replay path is byte-identical pre/post
(Hard Invariant #2 on BEAAA-533).
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import uuid
from typing import Any
from urllib.parse import urlsplit

# Deterministic namespace UUID for bundle ids (uuid5 over canonical request).
_BUNDLE_NS = uuid.UUID("c1c1f1c0-bea1-4bea-b001-bea1bea1bea1")
_DEV_KEY = b"v0.1-dev-key-do-not-use-in-prod"
_REDACTED = "<redacted>"


class BundleError(ValueError):
    pass


def _signing_key() -> bytes:
    raw = os.environ.get("ARE_BUNDLE_SIGNING_KEY")
    if raw:
        return raw.encode("utf-8")
    if os.environ.get("ARE_ALLOW_DEV_SIGNING_KEY") == "true":
        return _DEV_KEY
    raise ValueError("ARE_BUNDLE_SIGNING_KEY must be set in production")


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def derive_bundle_id(snapshot_hash: str, tenant_id: str, scored_at: str) -> str:
    """Deterministic bundle_id so same inputs → same id (red-line determinism)."""
    seed = f"{tenant_id}|{snapshot_hash}|{scored_at}"
    return str(uuid.uuid5(_BUNDLE_NS, seed))


def _sign(manifest: dict[str, Any]) -> str:
    mac = hmac.new(_signing_key(), _canonical(manifest), hashlib.sha256)
    return "hmac-sha256:" + mac.hexdigest()


def redact_scan_request(scan_request: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of `scan_request` with `credential_ref` redacted.

    Hard Invariant #4 boundary (BEAAA-533): the credential is resolved from
    the vault inside the Prober worker at scan time and never written to a
    forensic bundle, log line, or evidence URI. The bundle keeps the redacted
    ref so the evidence trail still shows that *a* credential was used,
    without recording which one.
    """
    redacted = copy.deepcopy(scan_request)
    if "credential_ref" in redacted:
        redacted["credential_ref"] = _REDACTED
    return redacted


def extract_target_endpoint_host(endpoint_url: str) -> str:
    """Return the host component of a target endpoint URL.

    NOT path, NOT query, NOT scheme — host only. Path and query may carry
    tokens or customer routing identifiers; the contract permits the host as
    evidence because the Underwriter needs to verify the scan reached a
    customer-controlled endpoint, but it does not permit the rest of the URL.
    """
    host = urlsplit(endpoint_url).hostname
    if not host:
        raise BundleError("target_endpoint_url has no parseable host")
    return host


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
        scan_request: dict[str, Any] | None = None,
        prober_version: str | None = None,
        target_endpoint_url: str | None = None,
    ) -> dict[str, Any]:
        """Build and persist a signed forensic bundle.

        The live-probe augmentations (`scan_request`, `prober_version`,
        `target_endpoint_url`) are all-or-nothing: pass either none (offline
        replay — bundle is byte-identical to v0.1) or all three (live probe —
        manifest carries the v1.1.0 evidence fields). The host is parsed
        from `target_endpoint_url` here, not at the caller, so the boundary
        cannot leak path or query into evidence.
        """
        live_probe_inputs = (scan_request, prober_version, target_endpoint_url)
        provided = [v is not None for v in live_probe_inputs]
        if any(provided) and not all(provided):
            raise BundleError(
                "live-probe bundle requires all of scan_request, prober_version, target_endpoint_url"
            )

        manifest: dict[str, Any] = {
            "bundle_id": bundle_id,
            "snapshot_hash": snapshot_hash,
            "evidence": evidence,
        }
        if scan_request is not None:
            manifest["scan_request"] = redact_scan_request(scan_request)
            manifest["prober_version"] = prober_version
            manifest["target_endpoint_host"] = extract_target_endpoint_host(
                target_endpoint_url
            )
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
    manifest: dict[str, Any] = {
        "bundle_id": bundle["bundle_id"],
        "snapshot_hash": bundle["snapshot_hash"],
        "evidence": bundle["evidence"],
    }
    for key in ("scan_request", "prober_version", "target_endpoint_host"):
        if key in bundle:
            manifest[key] = bundle[key]
    actual = _sign(manifest)
    if not hmac.compare_digest(actual, expected_sig):
        raise BundleError("signature mismatch")
