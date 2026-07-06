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
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from .config import ApiConfig

# Deterministic namespace UUID for internal idempotency keys (uuid5 over canonical request).
_BUNDLE_NS = uuid.UUID("c1c1f1c0-bea1-4bea-b001-bea1bea1bea1")
_REDACTED = "<redacted>"
# Used only when ARE_ALLOW_DEV_SIGNING_KEY=true; never acceptable in production.
_DEV_KEY = b"v0.1-dev-key-do-not-use-in-prod"


class BundleError(ValueError):
    pass


class BundleStorage(Protocol):
    """Storage backend for signed forensic bundles."""

    is_durable: bool

    def put(self, bundle_id: str, bundle: dict[str, Any]) -> None:
        ...

    def get(self, bundle_id: str) -> dict[str, Any] | None:
        ...

    def clear(self) -> None:
        ...


def _signing_key() -> bytes:
    return ApiConfig.from_env().bundle_signing_key(_DEV_KEY)


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def derive_bundle_id(snapshot_hash: str, tenant_id: str, scored_at: str) -> str:
    """Deterministic internal idempotency key for same-input bundle writes.

    Do not expose this value as the fetch handle. Public forensic-bundle ids
    are random opaque handles so callers cannot derive another tenant's bundle
    URI from known scan metadata.
    """
    seed = f"{tenant_id}|{snapshot_hash}|{scored_at}"
    return str(uuid.uuid5(_BUNDLE_NS, seed))


def new_opaque_bundle_id() -> str:
    """Return a random external forensic-bundle fetch handle."""
    return str(uuid.uuid4())


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


class InMemoryBundleStorage:
    """Development-only bundle storage backend."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self.is_durable = False

    def put(self, bundle_id: str, bundle: dict[str, Any]) -> None:
        self._store[bundle_id] = bundle

    def get(self, bundle_id: str) -> dict[str, Any] | None:
        return self._store.get(bundle_id)

    def clear(self) -> None:
        self._store.clear()


class FileBundleStorage:
    """Durable local filesystem backend for signed forensic bundles.

    This is the object-storage-compatible contract shape: a backend persists
    one signed JSON document by bundle id and returns the same document on
    read. Cloud object stores can implement the same small surface.
    """

    is_durable = True

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, bundle_id: str) -> Path:
        if "/" in bundle_id or "\\" in bundle_id or bundle_id in ("", ".", ".."):
            raise BundleError("invalid bundle_id for file storage")
        return self.root / f"{bundle_id}.json"

    def put(self, bundle_id: str, bundle: dict[str, Any]) -> None:
        path = self._path(bundle_id)
        payload = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode("utf-8")
        tmp = path.with_suffix(".json.tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, path)

    def get(self, bundle_id: str) -> dict[str, Any] | None:
        path = self._path(bundle_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def clear(self) -> None:
        for path in self.root.glob("*.json"):
            path.unlink()


class BundleStore:
    """Signed forensic bundle store with pluggable persistence."""

    def __init__(self, storage: BundleStorage | None = None) -> None:
        self.storage = storage or InMemoryBundleStorage()

    @property
    def is_in_memory(self) -> bool:
        return not self.storage.is_durable

    def build(
        self,
        *,
        bundle_id: str,
        tenant_id: str,
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
            "tenant_id": tenant_id,
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
        self.storage.put(bundle_id, bundle)
        return bundle

    def get(self, bundle_id: str, *, tenant_id: str) -> dict[str, Any] | None:
        bundle = self.storage.get(bundle_id)
        if bundle is None:
            return None
        # Verify-on-read: tampering detection at retrieval time, not just write.
        verify(bundle)
        if bundle.get("tenant_id") != tenant_id:
            return None
        return bundle

    def clear(self) -> None:
        self.storage.clear()


def verify(bundle: dict[str, Any]) -> None:
    """Raise BundleError if signature does not match manifest contents."""
    expected_sig = bundle.get("signed_manifest")
    if not isinstance(expected_sig, str):
        raise BundleError("missing signed_manifest")
    manifest: dict[str, Any] = {
        "bundle_id": bundle["bundle_id"],
        "tenant_id": bundle["tenant_id"],
        "snapshot_hash": bundle["snapshot_hash"],
        "evidence": bundle["evidence"],
    }
    for key in ("scan_request", "prober_version", "target_endpoint_host"):
        if key in bundle:
            manifest[key] = bundle[key]
    actual = _sign(manifest)
    if not hmac.compare_digest(actual, expected_sig):
        raise BundleError("signature mismatch")
