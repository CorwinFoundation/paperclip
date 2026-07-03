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

from pricing.quote import Ineligible, ManualReferral, QuoteError, Rates, quote
from rubric.scoring import Rubric, ScoringError, score
from underwriting import admittance

from .bundle import BundleStore, derive_bundle_id
from .intake_guard import IntakeError, enforce_hosted_eval_only, enforce_input_safety

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


# Public alias — BEAAA-546 T7 (single source of truth for tenant isolation
# across api/handlers.py and api/scan_handlers.py). Tests at
# tests/test_beaaa546_tenant_isolation_reuse.py + the scan-handler sentry
# at tests/test_scan_handlers_beaaa547.py assert identity with the private
# implementation; do not re-implement here.
verify_tenant_isolation = _verify_tenant_isolation


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
        enforce_input_safety(eval_outputs)
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
    # v0.2.3 (BEAAA-21, UW co-signed 2026-05-09): pull caller-asserted hints
    # off the request and the optional model_versions attestation off the
    # snapshot. corpus_version is the harness-actual stamp; in hosted-eval
    # intake mode the caller asserts it (the hosted runner ran the pack), so
    # the request value is treated as authoritative. Mismatch surfacing in
    # diagnostics is exercised by callers that pass both a server-actual and
    # a client-asserted value (covered in tests/test_v0_2_3_additive.py).
    client_corpus_version = body.get("corpus_version")
    client_seed = body.get("seed")
    snapshot_model_versions = snapshot.get("model_versions") or None
    try:
        result = score(
            rubric,
            engine_inputs,
            snapshot_hash=snapshot["snapshot_hash"],
            forensic_bundle_uri=forensic_bundle_uri,
            scored_at=scored_at,
            corpus_version=client_corpus_version,
            seed=client_seed,
            model_versions=snapshot_model_versions,
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

    # BEAAA-7937 (Pass-4 §1.1) — admittance enforcement on the production bind
    # path. ADR-0007 §1.3/§1.4: the admittance dial is a UW-side pre-condition
    # the pricing engine consumes. The /quote endpoint is the production quote
    # path, so it MUST construct an AdmittanceDecision and thread it into
    # quote(); otherwise REFUSE/REFER (ADM.R1-R6) and the ADM.R0 critical-dim
    # knockout (ADR-0038 / BEAAA-7888) are inert here and a non-bindable agent
    # rides the legacy D-band gate straight to a premium. system_prompt_kind is
    # not a Scanner contract output (ADR-0007 §1.4) — the UW submission record
    # carries it, so accept it off the request and fall back to the score
    # envelope when absent (R1/R4 simply do not fire without it).
    #
    # Contract-clean evidence handling (BEAAA-7939 UW co-sign condition):
    # admittance keys on the applicability record (measured_dims / na_dims).
    # The bare Scanner /score ScoreResponse does NOT carry it — the admittance
    # evidence bundle is assembled by the UW submission record (ADR-0007 §1.4).
    # If it is absent we MUST fail with an explicit contract error (422) rather
    # than let evaluate() silently fire ADM.R3 (under-5-measured) REFUSE — a
    # missing-evidence 422 must never be conflated with a genuine REFUSE.
    applicability = score_input.get("applicability_record") or score_input.get("applicability")
    if not isinstance(applicability, dict) or not isinstance(applicability.get("measured_dims"), list):
        raise HandlerError(
            422,
            "score.applicability_record.measured_dims required for admittance "
            "evaluation on the bind path (ADR-0007 §1.3/§1.4; assembled by the "
            "UW submission record, not the bare /score response)",
        )
    decision = admittance.evaluate(
        score_input,
        system_prompt_kind=body.get("system_prompt_kind"),
    )

    try:
        result = quote(
            score_input,
            customer,
            rates=rates,
            quoted_at=quoted_at,
            admittance_decision=decision,
        )
    except Ineligible as exc:
        # ADM REFUSE — no premium produced; decline with the firing-rule id.
        return 200, {
            "status": "ineligible",
            "reason": exc.reason,
            "firing_rule_id": exc.rule_id,
            "annual_premium_usd": None,
            "admittance": exc.decision,
            "breakdown": exc.breakdown,
            "pricing_version": rates.version,
        }
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


def validate_quote_tenant_context(
    body: dict[str, Any],
    *,
    authenticated_tenant_id: str | None,
) -> None:
    """Reject quote attempts that try to supply a different tenant in payload.

    The API edge authenticates tenant identity; request JSON may only echo it.
    This keeps quote-time pricing from trusting tenant ids supplied by the
    scored party.
    """
    if authenticated_tenant_id is None:
        return
    candidates: list[tuple[str, Any]] = [("tenant_id", body.get("tenant_id"))]
    score = body.get("score")
    if isinstance(score, dict):
        candidates.append(("score.tenant_id", score.get("tenant_id")))
    customer = body.get("customer")
    if isinstance(customer, dict):
        candidates.append(("customer.tenant_id", customer.get("tenant_id")))
    for path, value in candidates:
        if value in (None, ""):
            continue
        if str(value) != authenticated_tenant_id:
            raise HandlerError(409, f"tenant mismatch between auth context and {path}")
