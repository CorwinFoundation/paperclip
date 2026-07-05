"""Verification record builder — Trust Page v1 wiring (BEAAA-11926 / WI-3).

Assembles one partner-facing *verification record* per (agent, vendor) by wiring
the three real Phase-1 data sources named in the Trust Page spec (BEAAA-11917 §1):

  * BackBond Score band + verified-as-of + reproducibility envelope
        <- rubric.scoring.score() output on a LOCKED rubric (WI-1, BEAAA-11915).
  * Monitoring continuity  <- post_bind_ingest.continuity_gate (WI-2, BEAAA-11916).
  * Warranty remedy eligibility <- WI-6 §2 rule (BEAAA-11920) via warranty_eligibility.

Supreme Law: no record is emitted without a complete reproducibility envelope
(scanner_version, corpus_version, rubric_version, rubric locked_through, seed,
bands_version). ``build_record`` raises ``MissingEnvelopeError`` otherwise — a
score with no envelope is not a reportable score.

The stale-state model is the load-bearing feature (spec §2): a single monotonic
clock ``now - verified_as_of`` against the next-required-verification (NRV).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .warranty_eligibility import WarrantyEligibility

# --- stale-state model (spec §2) -------------------------------------------------
STALE_STATES = ("current", "aging", "due_soon", "stale", "expired")
# Fraction-of-cadence boundaries measured from verified_as_of toward NRV.
_AGING_AT = 0.60   # past freshness window
_DUE_SOON_AT = 0.85  # grace lead-up to NRV
# How far past NRV (in cadence multiples) before a lapsed record is called "expired".
_EXPIRED_AFTER = 2.0

# Required reproducibility-envelope fields. Missing any => not reportable.
REQUIRED_ENVELOPE_FIELDS = (
    "scanner_version",
    "corpus_version",
    "rubric_version",
    "rubric_locked_through",
    "seed",
    "bands_version",
)


class MissingEnvelopeError(ValueError):
    """Raised when a record would render without a complete provenance envelope."""


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def compute_stale_state(verified_as_of: datetime, nrv: datetime, now: datetime) -> str:
    """Map (verified_as_of, NRV, now) to a stale state per spec §2."""
    cadence = (nrv - verified_as_of).total_seconds()
    if cadence <= 0:
        raise ValueError("next_required_verification must be after verified_as_of")
    elapsed = (now - verified_as_of).total_seconds()
    frac = elapsed / cadence
    if now >= nrv:
        overshoot = (now - nrv).total_seconds() / cadence
        return "expired" if overshoot >= (_EXPIRED_AFTER - 1.0) else "stale"
    if frac >= _DUE_SOON_AT:
        return "due_soon"
    if frac >= _AGING_AT:
        return "aging"
    return "current"


def eligibility_effect(stale_state: str, warranty_status: str) -> str:
    """Warranty-eligibility *effect* of the stale state (spec §2), warranty-safe.

    Never upgrades warranty status; only reflects suspension when verification has
    lapsed. Returns a short status token for display.
    """
    if stale_state in ("stale", "expired"):
        return "suspended"  # eligibility suspended while verification is lapsed
    return warranty_status


@dataclass(frozen=True)
class VerificationRecord:
    agent_name: str
    vendor_name: str
    band_display: str
    band_posture: str
    verified_as_of: str            # ISO
    next_required_verification: str  # ISO
    stale_state: str
    monitoring_continuity: str
    warranty: dict[str, Any]
    warranty_effect: str
    envelope: dict[str, Any]
    evidence_refs: dict[str, Any] = field(default_factory=dict)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "vendor_name": self.vendor_name,
            "band_display": self.band_display,
            "band_posture": self.band_posture,
            "verified_as_of": self.verified_as_of,
            "next_required_verification": self.next_required_verification,
            "stale_state": self.stale_state,
            "monitoring_continuity": self.monitoring_continuity,
            "warranty": self.warranty,
            "warranty_effect": self.warranty_effect,
            "envelope": self.envelope,
            "evidence_refs": self.evidence_refs,
            "generated_at": self.generated_at,
        }


def build_envelope(score_response: dict[str, Any], rubric_locked_through: str) -> dict[str, Any]:
    """Extract the reproducibility envelope from a score() response + rubric lock."""
    band = score_response.get("band") or {}
    env = {
        "scanner_version": score_response.get("scanner_version"),
        "corpus_version": score_response.get("corpus_version"),
        "rubric_version": score_response.get("rubric_version"),
        "rubric_locked_through": rubric_locked_through,
        "seed": score_response.get("seed"),
        "bands_version": band.get("bands_version"),
        "contract_version": score_response.get("contract_version"),
        "model_versions": score_response.get("model_versions"),
        "snapshot_hash": score_response.get("snapshot_hash"),
    }
    return env


def _assert_envelope(env: dict[str, Any]) -> None:
    missing = [
        k for k in REQUIRED_ENVELOPE_FIELDS
        # corpus_version="" is a legitimate value for offline fixtures (B1); only
        # None/absent counts as missing.
        if env.get(k) is None
    ]
    if missing:
        raise MissingEnvelopeError(
            "reproducibility envelope incomplete; refusing to render record "
            f"(missing: {', '.join(missing)}). Supreme Law: no score without provenance."
        )


def build_record(
    *,
    agent_name: str,
    vendor_name: str,
    score_response: dict[str, Any],
    rubric_locked_through: str,
    continuity_status: str,
    warranty: WarrantyEligibility,
    verification_cadence_days: int,
    now: datetime,
    generated_at: str,
    evidence_refs: dict[str, Any] | None = None,
) -> VerificationRecord:
    """Assemble and validate a verification record from the three real sources."""
    band = score_response.get("band") or {}
    band_display = band.get("display")
    if not band_display:
        raise ValueError("score_response missing band.display")

    envelope = build_envelope(score_response, rubric_locked_through)
    _assert_envelope(envelope)  # Supreme Law gate

    verified_dt = _parse_dt(score_response["scored_at"])
    nrv_dt = verified_dt + timedelta(days=verification_cadence_days)
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)

    stale_state = compute_stale_state(verified_dt, nrv_dt, now)
    warranty_effect = eligibility_effect(stale_state, warranty.status)

    return VerificationRecord(
        agent_name=agent_name,
        vendor_name=vendor_name,
        band_display=band_display,
        band_posture=band.get("posture", ""),
        verified_as_of=verified_dt.isoformat(),
        next_required_verification=nrv_dt.isoformat(),
        stale_state=stale_state,
        monitoring_continuity=continuity_status,
        warranty=warranty.to_dict(),
        warranty_effect=warranty_effect,
        envelope=envelope,
        evidence_refs=evidence_refs or {},
        generated_at=generated_at,
    )
