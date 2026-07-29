"""evidence_receipt_v1 projection — BEAAA-18825 (Child 3 of BEAAA-18817).

Feature-gated, PII-free projection of the BackBond-signed ``evidence_receipt_v1``
object onto the two customer-facing result surfaces:

* the terminal webhook (``api/scan_handlers.py::_customer_result_response``), and
* the CI plugin (``api/ci_scan_ext.py::ci_plugin_result``).

The receipt itself is minted by the BackBond-controlled receipt service (Child 2,
BEAAA-18819) — never by the scanner or customer CI. This module ONLY *projects*
an already-minted receipt onto the customer surface, behind a feature gate,
enforcing the ratified ``evidence-receipt-v1`` schema **exactly** and a strict
PII allowlist so no raw logs, prompt/response content, customer names, or other
non-schema field can ever ride out on the projection.

Both callers route through :func:`project` so the projection logic lives in ONE
place (no duplicate schema/allowlist logic across the webhook and CI plugin).

Schema authority: ``contracts/evidence-receipt-v1.md`` §2 / ``docs/adr/
adr-0067-beaaa18817-evidence-receipt-v1-custody.md`` (RATIFIED 2026-07-29).
Additive to ``are-scanner-api-v1`` (patch v1.13.0); no scoring / rubric / corpus
/ band / weight change — golden N=5 delta = 0 by construction.
"""
from __future__ import annotations

import os
import re
from typing import Any, Mapping

# Feature gate (default OFF -> byte-identical prior contract response). Tier-2
# rollback = unset this flag (ADR-0067 §Rollback).
FEATURE_FLAG = "BACKBOND_EVIDENCE_RECEIPT_V1"

# --- evidence-receipt-v1 §2 schema (FROZEN by ADR-0067) --------------------
RECEIPT_VERSION = "evidence_receipt_v1"
SIGNATURE_ALG = "ed25519"

# EXACT field allowlist. A projected receipt must carry these keys and ONLY
# these keys: extras fail closed (PII / raw-evidence leak guard), missing keys
# fail closed (schema-incomplete). Order is the §2 schema order.
ALLOWED_FIELDS: tuple[str, ...] = (
    "version",
    "receipt_id",
    "scan_id",
    "tenant_ref",
    "evidence_bundle_sha256",
    "probe_manifest_sha256",
    "score_snapshot_sha256",
    "scanner_build_digest",
    "received_at",
    "signing_key_id",
    "signature_alg",
    "signature",
)
_ALLOWED_SET = frozenset(ALLOWED_FIELDS)

# The four SHA-256 digests + the scanner build digest are all ``sha256:<hex>``.
_SHA256_FIELDS: tuple[str, ...] = (
    "evidence_bundle_sha256",
    "probe_manifest_sha256",
    "score_snapshot_sha256",
    "scanner_build_digest",
)
# Non-empty opaque strings assigned by the receipt service / submission.
_STRING_FIELDS: tuple[str, ...] = (
    "receipt_id",
    "scan_id",
    "tenant_ref",
    "received_at",
    "signing_key_id",
    "signature",
)
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# §6 fail-closed marker: gate on, but the receipt service did not mint a receipt
# (unreachable / key unavailable / signing failure / not yet issued). The
# scanner NEVER self-signs or fabricates a receipt; it reports the absence.
RECEIPT_STATUS_KEY = "receipt_status"
RECEIPT_STATUS_UNAVAILABLE = "unavailable"

__all__ = [
    "FEATURE_FLAG",
    "RECEIPT_VERSION",
    "SIGNATURE_ALG",
    "ALLOWED_FIELDS",
    "RECEIPT_STATUS_KEY",
    "RECEIPT_STATUS_UNAVAILABLE",
    "enabled",
    "is_valid_receipt",
    "project",
]


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True when the ``evidence_receipt`` projection feature gate is on."""
    source = os.environ if env is None else env
    return source.get(FEATURE_FLAG, "").strip().lower() in {"1", "true", "yes"}


def is_valid_receipt(receipt: Mapping[str, Any]) -> bool:
    """True iff ``receipt`` matches the evidence-receipt-v1 §2 schema exactly.

    Exact-match (not subset): the key set must equal the allowlist. Extra keys
    fail (PII/raw-evidence leak guard); missing keys fail (schema-incomplete).
    """
    if not isinstance(receipt, Mapping):
        return False
    if set(receipt.keys()) != _ALLOWED_SET:
        return False
    if receipt.get("version") != RECEIPT_VERSION:
        return False
    if receipt.get("signature_alg") != SIGNATURE_ALG:
        return False
    for field in _SHA256_FIELDS:
        value = receipt.get(field)
        if not isinstance(value, str) or _SHA256_RE.match(value) is None:
            return False
    for field in _STRING_FIELDS:
        value = receipt.get(field)
        if not isinstance(value, str) or not value:
            return False
    return True


def project(
    receipt: Mapping[str, Any] | None,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return the keys to MERGE into a customer-facing result response.

    * gate OFF                          -> ``{}`` (byte-identical prior response)
    * gate ON, valid receipt            -> ``{"evidence_receipt": {<12 fields>}}``
    * gate ON, absent / invalid receipt -> ``{"receipt_status": "unavailable"}``
      (§6 fail-closed — never fabricate, never leak)

    The emitted ``evidence_receipt`` is a whitelist copy of exactly the §2
    fields in schema order, so a receipt carrying any non-schema key (a customer
    name, a raw log, a bundle URI) fails closed rather than leaking it.
    """
    if not enabled(env):
        return {}
    if is_valid_receipt(receipt):  # type: ignore[arg-type]
        clean = {field: receipt[field] for field in ALLOWED_FIELDS}  # type: ignore[index]
        return {"evidence_receipt": clean}
    return {RECEIPT_STATUS_KEY: RECEIPT_STATUS_UNAVAILABLE}
