"""BEAAA-16490 cross-provider continuity decision engine.

Resolves per-provider BackBond determinations from two or more independent
provider sources into exactly ONE continuity band and ONE continuity decision
for a single deployed agent, without letting either provider determine the
outcome.

Pipeline::

    provider envelopes (>=2 independent sources)
        -> normalize_provider_evidence  (provenance preserved, PII redacted)
        -> DeterminationEngine.evaluate  (deterministic BackBond rules, unchanged)
        -> per-provider BackBond signal
        -> deterministic continuity policy (corroborate / diverge / degrade)
        -> ONE band + ONE continuity decision + machine-verifiable bundle

Invariants enforced here:

* The deciding inputs are the BackBond determination signals ONLY. The
  provider-native determination/attestation lives in provenance and is never
  read by the resolver — a provider verdict can never override the outcome.
* Conflicting authenticated sources emit a DIVERGENCE action; they are never
  averaged and neither provider breaks the tie.
* Missing / stale / unauthenticated / non-independent sources drop the outcome
  to the DEGRADED lane (fail-closed), never to a silent single-provider ruling.
* No BackBond Score math, rubric, band cuts, or are-scanner-api response
  semantics change; this is additive evidence-plane resolution. Rollback =
  revert the isolated new files.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from determination import DETERMINATION_ENGINE_VERSION
from determination.engine import DeterminationEngine
from determination.provider_normalization import (
    IndependenceResult,
    NormalizedProviderInput,
    ProviderEvidenceEnvelope,
    check_independence,
    normalize_provider_evidence,
)
from determination.records import (
    DECISION_COVERED_CANDIDATE,
    DECISION_INSUFFICIENT_EVIDENCE,
    DECISION_NO_FINDING,
    DeterminationRecord,
)
from determination.rule_dsl import canonical_json_bytes

_POLICY_DIR = Path(__file__).resolve().parent / "rules"
_DEFAULT_POLICY_PATH = _POLICY_DIR / "continuity_policy_v1.yaml"

CONTINUITY_SCHEMA_VERSION = "continuity-decision-bundle@1.0"

# Per-provider BackBond signals (derived from determination records).
SIGNAL_CANDIDATE = "covered_peril_candidate"
SIGNAL_INSUFFICIENT = "insufficient_evidence"
SIGNAL_NO_FINDING = "no_finding"

# Terminal bands / decisions (mirror continuity_policy_v1.yaml `bands`).
BAND_CONTINUE = "CONTINUE"
BAND_ELEVATED = "ELEVATED"
BAND_DIVERGENCE = "DIVERGENCE"
BAND_DEGRADED = "DEGRADED"

DECISION_CONTINUE = "continue_monitoring"
DECISION_REVIEW = "review_covered_peril_candidate"
DECISION_DIVERGENCE = "divergence_hold"
DECISION_DEGRADED = "degraded_insufficient_independent_corroboration"


class ContinuityError(ValueError):
    pass


@dataclass(frozen=True)
class ContinuityPolicy:
    version: str
    freshness_window_seconds: int
    min_independent_corroborating_sources: int
    band_decisions: Mapping[str, str]

    @classmethod
    def load(cls, path: Path | None = None) -> "ContinuityPolicy":
        raw = yaml.safe_load(
            (path or _DEFAULT_POLICY_PATH).read_text(encoding="utf-8")
        )
        if not isinstance(raw, Mapping):
            raise ContinuityError("continuity policy file is not a mapping")
        version = raw.get("policy_version")
        window = raw.get("freshness_window_seconds")
        min_sources = raw.get("min_independent_corroborating_sources")
        bands = raw.get("bands")
        if not isinstance(version, str) or not version:
            raise ContinuityError("policy_version missing")
        if not isinstance(window, int) or window <= 0:
            raise ContinuityError("freshness_window_seconds must be a positive int")
        if not isinstance(min_sources, int) or min_sources < 2:
            raise ContinuityError(
                "min_independent_corroborating_sources must be >= 2"
            )
        if not isinstance(bands, Mapping) or not bands:
            raise ContinuityError("bands table missing")
        band_decisions = {
            str(name): str(spec["decision"]) for name, spec in bands.items()
        }
        return cls(
            version=version,
            freshness_window_seconds=window,
            min_independent_corroborating_sources=min_sources,
            band_decisions=band_decisions,
        )


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _provider_signal(records: Sequence[DeterminationRecord]) -> str:
    """Collapse one provider's determination records to a single BackBond signal.

    Precedence: covered_peril_candidate > insufficient_evidence > no_finding.
    """
    decisions = {r.final_decision for r in records}
    if DECISION_COVERED_CANDIDATE in decisions:
        return SIGNAL_CANDIDATE
    if DECISION_INSUFFICIENT_EVIDENCE in decisions:
        return SIGNAL_INSUFFICIENT
    return SIGNAL_NO_FINDING


@dataclass(frozen=True)
class SourceAssessment:
    """One provider source after normalization + determination, ready to resolve."""

    provenance: dict[str, Any]
    signal: str
    authenticated: bool
    fresh: bool
    staleness_seconds: float
    eligible: bool
    determination_ids: tuple[str, ...]
    provenance_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provenance": self.provenance,
            "backbond_signal": self.signal,
            "authenticated": self.authenticated,
            "fresh": self.fresh,
            "staleness_seconds": self.staleness_seconds,
            "eligible": self.eligible,
            "determination_ids": list(self.determination_ids),
            "provenance_digest": self.provenance_digest,
        }


@dataclass(frozen=True)
class ContinuityDecision:
    """ONE band + ONE decision resolved across all provider sources."""

    agent_id: str
    agent_version: str
    as_of: str
    band: str
    decision: str
    rationale: str
    policy_version: str
    normalizer_version: str
    engine_version: str
    rule_set_version: str
    sources: tuple[SourceAssessment, ...]
    independence: dict[str, Any]
    eligible_source_count: int
    bundle_sha256: str

    def to_bundle(self) -> dict[str, Any]:
        """Full machine-verifiable evidence bundle (preimage + bound hash)."""
        bundle = dict(_bundle_preimage(self))
        bundle["bundle_sha256"] = self.bundle_sha256
        return bundle


def _bundle_preimage(decision: "ContinuityDecision") -> dict[str, Any]:
    """Canonical, hash-bound preimage. Binds the resolved outcome to every
    source's preserved provenance, BackBond signal, and determination ids.

    The provider-native determination is inside each source's ``provenance`` for
    audit, but the resolver never reads it, so it cannot change the hash-bound
    outcome — proven by :func:`recompute_decision`.
    """
    return {
        "schema_version": CONTINUITY_SCHEMA_VERSION,
        "agent_id": decision.agent_id,
        "agent_version": decision.agent_version,
        "as_of": decision.as_of,
        "band": decision.band,
        "decision": decision.decision,
        "policy_version": decision.policy_version,
        "normalizer_version": decision.normalizer_version,
        "engine_version": decision.engine_version,
        "rule_set_version": decision.rule_set_version,
        "independence": decision.independence,
        "sources": sorted(
            (s.to_dict() for s in decision.sources),
            key=lambda s: str(s["provenance"]["source_provider_id"]),
        ),
    }


def _bundle_sha256(preimage: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(preimage)).hexdigest()


class ContinuityDecisionEngine:
    """Resolves cross-provider evidence into one continuity band + decision."""

    def __init__(
        self,
        *,
        policy: ContinuityPolicy | None = None,
        determination_engine: DeterminationEngine | None = None,
    ) -> None:
        self.policy = policy or ContinuityPolicy.load()
        self.engine = determination_engine or DeterminationEngine()

    def decide(
        self,
        *,
        agent_id: str,
        agent_version: str,
        envelopes: Sequence[ProviderEvidenceEnvelope | Mapping[str, Any]],
        as_of: str,
    ) -> ContinuityDecision:
        as_of_dt = _parse_iso(as_of)
        normalized: list[NormalizedProviderInput] = []
        for env in envelopes:
            envelope = (
                env
                if isinstance(env, ProviderEvidenceEnvelope)
                else ProviderEvidenceEnvelope.from_dict(env)
            )
            norm = normalize_provider_evidence(envelope)
            # Lineage guard: every source must be about the SAME agent, else the
            # cross-provider comparison is meaningless. Fail closed.
            if (
                norm.packet.agent_id != agent_id
                or norm.packet.agent_version != agent_version
            ):
                raise ContinuityError(
                    f"provider {envelope.source_provider_id!r} evidence is for "
                    f"{norm.packet.agent_id}/{norm.packet.agent_version}, not "
                    f"{agent_id}/{agent_version}"
                )
            normalized.append(norm)

        assessments = tuple(
            self._assess(norm, as_of_dt) for norm in normalized
        )
        eligible_inputs = [
            norm
            for norm, a in zip(normalized, assessments)
            if a.eligible
        ]
        independence = check_independence(eligible_inputs)
        band, rationale = _resolve_band(
            assessments, independence, self.policy
        )
        decision = self.policy.band_decisions[band]

        partial = ContinuityDecision(
            agent_id=agent_id,
            agent_version=agent_version,
            as_of=as_of_dt.isoformat().replace("+00:00", "Z"),
            band=band,
            decision=decision,
            rationale=rationale,
            policy_version=self.policy.version,
            normalizer_version=normalized[0].normalizer_version
            if normalized
            else "provider-normalizer@1.0.0",
            engine_version=DETERMINATION_ENGINE_VERSION,
            rule_set_version=self.engine.rule_set.version,
            sources=assessments,
            independence=independence.to_dict(),
            eligible_source_count=len(eligible_inputs),
            bundle_sha256="",
        )
        bundle_sha = _bundle_sha256(_bundle_preimage(partial))
        return ContinuityDecision(
            **{**partial.__dict__, "bundle_sha256": bundle_sha}
        )

    def _assess(
        self, norm: NormalizedProviderInput, as_of_dt: datetime
    ) -> SourceAssessment:
        records = self.engine.evaluate(norm.packet)
        signal = _provider_signal(records)
        event_dt = _parse_iso(norm.event_timestamp)
        staleness = (as_of_dt - event_dt).total_seconds()
        authenticated = norm.provenance.authenticated()
        fresh = 0 <= staleness <= self.policy.freshness_window_seconds
        return SourceAssessment(
            provenance=norm.provenance.to_dict(),
            signal=signal,
            authenticated=authenticated,
            fresh=fresh,
            staleness_seconds=staleness,
            eligible=authenticated and fresh,
            determination_ids=tuple(r.determination_id for r in records),
            provenance_digest=norm.provenance_digest(),
        )


def _resolve_band(
    assessments: Sequence[SourceAssessment],
    independence: IndependenceResult,
    policy: ContinuityPolicy,
) -> tuple[str, str]:
    """Pure deterministic resolver. Reads ONLY BackBond signals + eligibility +
    independence — never the provider-native determination.
    """
    eligible = [a for a in assessments if a.eligible]
    min_sources = policy.min_independent_corroborating_sources

    if len(eligible) < min_sources or not independence.independent:
        return (
            BAND_DEGRADED,
            f"{len(eligible)} eligible independent source(s); "
            f"need {min_sources} and independence="
            f"{independence.independent} ({independence.reason})",
        )

    signals = [a.signal for a in eligible]
    has_candidate = SIGNAL_CANDIDATE in signals
    has_no_finding = SIGNAL_NO_FINDING in signals

    if has_candidate and has_no_finding:
        return (
            BAND_DIVERGENCE,
            "independent authenticated sources conflict "
            "(covered_peril_candidate vs no_finding); divergence action emitted, "
            "no averaging, neither provider breaks the tie",
        )
    if has_candidate:
        corroborating = sum(1 for s in signals if s == SIGNAL_CANDIDATE)
        if corroborating >= min_sources:
            return (
                BAND_ELEVATED,
                f"{corroborating} independent authenticated sources corroborate a "
                "covered_peril_candidate; routed to human review lane",
            )
        return (
            BAND_DEGRADED,
            f"covered_peril_candidate not independently corroborated "
            f"({corroborating} candidate vs {min_sources} required)",
        )
    corroborating_clean = sum(1 for s in signals if s == SIGNAL_NO_FINDING)
    if corroborating_clean >= min_sources:
        return (
            BAND_CONTINUE,
            f"{corroborating_clean} independent authenticated sources corroborate "
            "no covered-peril finding; continuity holds",
        )
    return (
        BAND_DEGRADED,
        "no covered-peril finding is not independently corroborated "
        "(insufficient-evidence sources do not corroborate)",
    )


def recompute_decision(
    bundle: Mapping[str, Any], *, policy: ContinuityPolicy | None = None
) -> dict[str, Any]:
    """Re-derive band/decision from a bundle's PRESERVED per-source signals.

    Machine-verification path: recomputes the outcome from the bundle alone,
    using only the preserved BackBond signals + eligibility + independence, and
    re-checks the bound hash. This proves (a) the bundle is self-verifying and
    (b) the provider-native determination inside each source's provenance played
    no part in the decision.
    """
    policy = policy or ContinuityPolicy.load()
    sources = bundle.get("sources", [])
    assessments = tuple(
        SourceAssessment(
            provenance=dict(s["provenance"]),
            signal=str(s["backbond_signal"]),
            authenticated=bool(s["authenticated"]),
            fresh=bool(s["fresh"]),
            staleness_seconds=float(s["staleness_seconds"]),
            eligible=bool(s["eligible"]),
            determination_ids=tuple(s.get("determination_ids", [])),
            provenance_digest=str(s.get("provenance_digest", "")),
        )
        for s in sources
    )
    ind = bundle.get("independence", {})
    independence = IndependenceResult(
        independent=bool(ind.get("independent")),
        reason=str(ind.get("reason", "")),
        distinct_providers=tuple(ind.get("distinct_providers", [])),
        distinct_stacks=tuple(ind.get("distinct_stacks", [])),
        distinct_attestation_keys=tuple(ind.get("distinct_attestation_keys", [])),
    )
    band, rationale = _resolve_band(assessments, independence, policy)
    decision = policy.band_decisions[band]

    preimage = {k: v for k, v in bundle.items() if k != "bundle_sha256"}
    recomputed_hash = _bundle_sha256(preimage)

    return {
        "band": band,
        "decision": decision,
        "rationale": rationale,
        "band_matches": band == bundle.get("band"),
        "decision_matches": decision == bundle.get("decision"),
        "hash_matches": recomputed_hash == bundle.get("bundle_sha256"),
        "recomputed_bundle_sha256": recomputed_hash,
    }


def verify_bundle(bundle: Mapping[str, Any], *, policy: ContinuityPolicy | None = None) -> bool:
    """True iff the bundle's band, decision, and bound hash all recompute."""
    result = recompute_decision(bundle, policy=policy)
    return bool(
        result["band_matches"]
        and result["decision_matches"]
        and result["hash_matches"]
    )


__all__ = [
    "BAND_CONTINUE",
    "BAND_DEGRADED",
    "BAND_DIVERGENCE",
    "BAND_ELEVATED",
    "CONTINUITY_SCHEMA_VERSION",
    "ContinuityDecision",
    "ContinuityDecisionEngine",
    "ContinuityError",
    "ContinuityPolicy",
    "SIGNAL_CANDIDATE",
    "SIGNAL_INSUFFICIENT",
    "SIGNAL_NO_FINDING",
    "SourceAssessment",
    "recompute_decision",
    "verify_bundle",
]
