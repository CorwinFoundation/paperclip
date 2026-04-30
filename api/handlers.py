"""Pure handler logic for /score and /forensic_bundle/{bundleId} (Task A).

Handlers are framework-agnostic — they accept dicts + a tenant context and
return (status, body) tuples. The FastAPI app in api/app.py is a thin adapter.
This split keeps red-line tests stdlib-only and lets us swap framework without
re-testing snapshot integrity, tenant isolation, intake-guard semantics.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pricing.quote import ManualReferral, QuoteError, Rates, quote
from rubric.scoring import Rubric, ScoringError, score

from .bundle import BundleStore, derive_bundle_id
from .intake_guard import IntakeError, enforce_hosted_eval_only

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.IGNORECASE)


class HandlerError(Exception):
    """Carries the HTTP status code the API edge should return."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _verify_snapshot_integrity(snapshot: dict[str, Any]) -> None:
    for field in ("model_id", "model_version", "prompt_version", "snapshot_hash"):
        if not snapshot.get(field):
            raise HandlerError(422, f"snapshot.{field} required")
    if not _SHA256_RE.match(snapshot["snapshot_hash"]):
        raise HandlerError(422, "snapshot_hash must match sha256:<64-hex>")


def _verify_tenant_isolation(body_tenant: str, header_tenant: str | None) -> None:
    if not header_tenant:
        raise HandlerError(409, "X-Tenant-Id header required for tenant isolation")
    if body_tenant != header_tenant:
        raise HandlerError(409, "tenant mismatch between header and body")


def score_handler(
    body: dict[str, Any],
    *,
    tenant_header: str | None,
    rubric: Rubric,
    bundles: BundleStore,
    scored_at: datetime | None = None,
) -> tuple[int, dict[str, Any]]:
    """Process a /score request. Returns (status, response_body)."""
    for field in ("tenant_id", "snapshot", "eval_outputs"):
        if field not in body:
            raise HandlerError(422, f"{field} required")

    snapshot = body["snapshot"]
    if not isinstance(snapshot, dict):
        raise HandlerError(422, "snapshot must be an object")
    _verify_snapshot_integrity(snapshot)
    _verify_tenant_isolation(body["tenant_id"], tenant_header)

    eval_outputs = body["eval_outputs"]
    try:
        enforce_hosted_eval_only(eval_outputs)
    except IntakeError as exc:
        raise HandlerError(422, f"intake rejected: {exc}") from exc

    scored_at = scored_at or datetime.now(timezone.utc)
    scored_at_iso = scored_at.isoformat()
    bundle_id = derive_bundle_id(
        snapshot_hash=snapshot["snapshot_hash"],
        tenant_id=body["tenant_id"],
        scored_at=scored_at_iso,
    )
    forensic_bundle_uri = f"https://scanner.beai.local/v0/forensic_bundle/{bundle_id}"

    engine_inputs = {
        dim_id: {"raw": entry["raw"], "evidence_uri": entry.get("evidence_uri", "")}
        for dim_id, entry in eval_outputs.items()
    }
    try:
        result = score(
            rubric,
            engine_inputs,
            snapshot_hash=snapshot["snapshot_hash"],
            forensic_bundle_uri=forensic_bundle_uri,
            scored_at=scored_at,
        )
    except ScoringError as exc:
        raise HandlerError(422, f"scoring rejected: {exc}") from exc

    evidence = [
        {
            "dim_id": dim_id,
            "uri": entry.get("evidence_uri", ""),
            "sha256": entry.get("evidence_sha256", ""),
        }
        for dim_id, entry in eval_outputs.items()
    ]
    bundles.build(
        bundle_id=bundle_id,
        snapshot_hash=snapshot["snapshot_hash"],
        evidence=evidence,
    )
    return 200, result


def forensic_bundle_handler(
    bundle_id: str,
    *,
    bundles: BundleStore,
) -> tuple[int, dict[str, Any]]:
    """Process a /forensic_bundle/{bundleId} request."""
    bundle = bundles.get(bundle_id)
    if bundle is None:
        raise HandlerError(404, "bundle not found")
    return 200, bundle


def quote_handler(
    body: dict[str, Any],
    *,
    rates: Rates,
    quoted_at: datetime | None = None,
) -> tuple[int, dict[str, Any]]:
    """Process a /quote request — apply pricing-v1 formula to a scanner result + customer."""
    for field in ("score", "customer"):
        if field not in body:
            raise HandlerError(422, f"{field} required")
    score_input = body["score"]
    customer = body["customer"]
    if not isinstance(score_input, dict) or not isinstance(customer, dict):
        raise HandlerError(422, "score and customer must be objects")

    try:
        result = quote(score_input, customer, rates=rates, quoted_at=quoted_at)
    except ManualReferral as exc:
        return 200, {
            "status": "manual_referral",
            "reason": exc.reason,
            "annual_premium_usd": None,
            "breakdown": exc.breakdown,
            "pricing_version": rates.version,
        }
    except QuoteError as exc:
        raise HandlerError(422, f"quote rejected: {exc}") from exc
    return 200, result
