"""BEAAA-796 — fused `verdict` envelope + four MUST-emit promotions.

Pure, additive renderer that turns a `ScoreResponse` (substrate side) plus an
optional P4 attestation fixture into the v1.0.3 `verdict` envelope. Contract:
`contracts/are-scanner-api-v1.0.3-patch.md@v1.0.3`. Co-sign matrix on parent
BEAAA-767: HoUW BEAAA-766 (binding) + UW BEAAA-765 (conditional) + CTO
BEAAA-763 + CEO ack `f8505b4f`. Founder NOT required.

No scoring-math here. The envelope is derived at packet-render time from the
pillar scores already produced by `rubric.scoring.score` plus the customer's
P4 attestation (read off `governance_attestations.json` at the CLI surface).

Watchpoints per HoUW BEAAA-766 (any trip ⇒ halt merge, escalate CTO + UW +
HoUW for fresh co-sign on a v1.0.4+ ticket):

  1. `verdict.selection_rule` deviation from "min(substrate, attestation)".
  2. Semantics change to `worst_case_loaded.applies_uw_guarantee_2`.
  3. Enum widening on `attestation.review_state`.
  4. ≥1pt golden drift on any fixture.
  5. Pillar-weight plumbing leak via field addition.
  6. Breaking `baseline.json` rename.
"""
from __future__ import annotations

from typing import Any, Iterable

# Public enum values — keep tight so widening is a static-grep flag.
BIND_ELIGIBILITY = ("eligible", "ineligible_attestation", "ineligible_substrate", "review_required")
REVIEW_STATES = ("unreviewed", "uw_reviewed", "uw_rejected")
ROW_VERDICTS = ("pass", "partial", "fail", "n/a")
SELECTION_RULE = "min(substrate, attestation)"

_AGENT_PRIOR_PREFIX = "scripts/agent_priors/"
_AGENT_PRIOR_SUFFIX = ".json"

# BEAAA-214 Q3 raw_self_report tier-2 cap — applied mechanically when
# `review_state == "unreviewed"`. Tier-2 cap means any tier>2 row collapses
# to 2 for the unreviewed scoring; rows already at <=2 (e.g. P4.5=0 when no
# eval ledger) pass through unchanged.
_RAW_SELF_REPORT_TIER_CAP = 2

# P4 attestation dim id <-> fixture key. Fixture uses `P4_1` naming
# (underscore); the rubric uses `P4.1` (dot). Both forms surface in
# downstream code; we accept either.
_P4_DIMS = ("P4.1", "P4.2", "P4.3", "P4.4", "P4.5", "P4.6")


def _floor_str(value: float) -> str:
    """Render a pillar floor without trailing zeros (`12.5` not `12.50`).

    Mirrors the reference-packet text — keeps the verdict primary_reasons
    string byte-stable when the floor is a clean .5 / .25 value.
    """
    s = f"{value:g}"
    return s


def _is_agent_prior_uri(evidence_uri: str | None) -> bool:
    if not evidence_uri or not isinstance(evidence_uri, str):
        return False
    return _AGENT_PRIOR_PREFIX in evidence_uri and evidence_uri.endswith(_AGENT_PRIOR_SUFFIX)


def _row_verdict_for_raw(raw: float | None) -> str:
    """Per-row verdict enum used in `evidence_refs[]` rows.

    Mirrors the existing cli_runner threshold (raw>=3 pass, >=1.5 partial,
    else fail) so the four-MUST-emit promotion is byte-stable with what
    cli_runner has been emitting since BEAAA-464. `None` raw (applicable=
    false dims) returns `"n/a"`.
    """
    if raw is None:
        return "n/a"
    if raw >= 3.0:
        return "pass"
    if raw >= 1.5:
        return "partial"
    return "fail"


def _per_dim_floor(pillar_id: str, pillar_floors: dict[str, Any]) -> float | None:
    """Per-dim floor used when collapsing a priored dim to worst case.

    Per `contracts/are-scanner-api-v1.0.3-patch.md` §Semantics — collapse
    rule is: `0 for P4 dims with no measured floor; per-pillar floor per
    pillar_floors[*].min_dim_raw where defined`. GOVERNANCE is the P4
    pillar — explicit 0 floor for collapse. Other pillars use the
    `min_dim_raw` value when declared; pillars without one keep the dim
    raw unchanged (no defined floor → no collapse).
    """
    if pillar_id == "GOVERNANCE":
        return 0.0
    floor = pillar_floors.get(pillar_id) or {}
    if "min_dim_raw" in floor:
        return float(floor["min_dim_raw"])
    return None


def _compute_worst_case_loaded(
    pillars: dict[str, Any],
    pillar_floors: dict[str, Any],
) -> dict[str, Any]:
    """Recompute pillar scores under priors-collapse semantics.

    Returns
    -------
    dict
        Carries the recomputed are_total / governance_subscore alongside
        `applies_uw_guarantee_2` (true ⇔ any dim collapsed), a `rationale`
        string keyed to the dims that collapsed, plus the per-pillar
        recomputed `score` and per-dim `raw` for downstream auditing.
    """
    any_collapsed = False
    collapsed_dims_by_pillar: dict[str, list[str]] = {}
    new_pillars: dict[str, Any] = {}
    total = 0.0
    for pid, pdata in pillars.items():
        weight = int(pdata.get("weight", 0))
        dim_block = pdata.get("dimensions", {}) or {}
        per_dim_floor = _per_dim_floor(pid, pillar_floors)
        new_dims: dict[str, dict[str, Any]] = {}
        raws_for_mean: list[float] = []
        for did, drow in dim_block.items():
            raw = drow.get("raw")
            evidence_uri = drow.get("evidence_uri", "") or ""
            applicable = drow.get("applicable", True)
            # N/A dims are excluded from the pillar mean both in the
            # optimistic and worst-case views (Option C semantics);
            # carry them through with raw=None so downstream consumers
            # can see they didn't contribute.
            if applicable is False or raw is None:
                new_dims[did] = {"raw": None, "collapsed": False, "evidence_uri": evidence_uri}
                continue
            raw_f = float(raw)
            if _is_agent_prior_uri(evidence_uri) and per_dim_floor is not None:
                # Collapse this dim to its declared floor.
                collapsed_raw = float(per_dim_floor)
                new_dims[did] = {
                    "raw": collapsed_raw,
                    "collapsed": True,
                    "evidence_uri": evidence_uri,
                }
                raws_for_mean.append(collapsed_raw)
                any_collapsed = True
                collapsed_dims_by_pillar.setdefault(pid, []).append(did)
            else:
                new_dims[did] = {
                    "raw": raw_f,
                    "collapsed": False,
                    "evidence_uri": evidence_uri,
                }
                raws_for_mean.append(raw_f)
        if raws_for_mean:
            normalized = (sum(raws_for_mean) / len(raws_for_mean)) / 4.0
        else:
            normalized = 0.0
        pillar_score = normalized * weight
        new_pillars[pid] = {
            "score": round(pillar_score, 4),
            "weight": weight,
            "dimensions": new_dims,
        }
        total += pillar_score

    are_total_int = int(round(total))
    gov_subscore = round(new_pillars.get("GOVERNANCE", {}).get("score", 0.0), 4)

    if collapsed_dims_by_pillar:
        parts: list[str] = []
        for pid in sorted(collapsed_dims_by_pillar):
            dims = sorted(collapsed_dims_by_pillar[pid])
            floor = _per_dim_floor(pid, pillar_floors)
            parts.append(f"{pid}:{','.join(dims)}→{floor}")
        rationale = (
            f"Collapsed priored dims to per-dim floor under UW guarantee 2: {'; '.join(parts)}. "
            "Family-level priors (scripts/agent_priors/*.json) are not deployment-measured; "
            "worst-case load per `are-scanner-api-v1@v1.0.3` §Hard guarantees-UW #2."
        )
    else:
        rationale = (
            "No dim resolves under scripts/agent_priors/*.json; substrate is "
            "fully deployment-measured. UW guarantee 2 worst-case loading not triggered."
        )

    return {
        "are_total": are_total_int,
        "governance_subscore": gov_subscore,
        "applies_uw_guarantee_2": any_collapsed,
        "rationale": rationale,
        "collapsed_dims_by_pillar": collapsed_dims_by_pillar,
        "pillars": new_pillars,
    }


def _attestation_dim_tier(attestation_fixture: dict[str, Any], dim_id: str) -> int:
    """Resolve a P4 dim's claimed tier from the attestation fixture.

    Fixture keys use the underscore form (`P4_1`); rubric uses `P4.1`.
    The fixture's `_metadata.expected_form_claimed_tiers` is the
    operator-claimed tier ladder; absent => 0. P4.5 has no
    claimed-tier entry in `expected_form_claimed_tiers` per the
    BEAAA-200 pilot fixture (no eval ledger → tier 0).
    """
    meta = (attestation_fixture or {}).get("_metadata") or {}
    claimed = meta.get("expected_form_claimed_tiers") or {}
    if dim_id in claimed:
        try:
            return int(claimed[dim_id])
        except (TypeError, ValueError):
            return 0
    underscore = dim_id.replace(".", "_")
    if underscore in claimed:
        try:
            return int(claimed[underscore])
        except (TypeError, ValueError):
            return 0
    return 0


def _attestation_dim_tiers(
    attestation_fixture: dict[str, Any] | None,
    review_state: str,
) -> dict[str, int]:
    """Apply BEAAA-214 Q3 raw_self_report tier-2 cap mechanically.

    For `review_state == "unreviewed"`, every claimed tier is capped at 2
    (raw_self_report cap). For `uw_reviewed`, claimed tiers pass through.
    For `uw_rejected`, every tier collapses to 0.
    """
    out: dict[str, int] = {}
    for did in _P4_DIMS:
        if attestation_fixture is None:
            out[did] = 0
            continue
        claimed = _attestation_dim_tier(attestation_fixture, did)
        if review_state == "uw_rejected":
            out[did] = 0
        elif review_state == "unreviewed":
            out[did] = min(claimed, _RAW_SELF_REPORT_TIER_CAP)
        else:  # uw_reviewed
            out[did] = claimed
    return out


def _attestation_governance_subscore(
    attestation_fixture: dict[str, Any] | None,
    review_state: str,
    pillars: dict[str, Any],
) -> float:
    """Compute the attestation-side governance subscore.

    Mirrors the GOVERNANCE pillar mean: sum(tiers) / n_dims / 4 * weight.
    Reads the weight from the substrate `pillars["GOVERNANCE"]["weight"]`
    so a future pillar-weight change at the rubric stays in lockstep.
    """
    weight = int(pillars.get("GOVERNANCE", {}).get("weight", 25))
    n_dims = len(_P4_DIMS)
    tiers = _attestation_dim_tiers(attestation_fixture, review_state)
    raw_sum = sum(tiers.values())
    normalized = (raw_sum / n_dims) / 4.0
    return round(normalized * weight, 3)


def _resolve_review_state(attestation_fixture: dict[str, Any] | None) -> str:
    """Pull review_state off the attestation fixture, defaulting to unreviewed.

    The attestation fixture format may carry `review_state` at the top
    level (BEAAA-200 pilot v0.1.0 default → unreviewed). Enum widening
    triggers HoUW per BEAAA-766 forward watchpoint #3.
    """
    if not attestation_fixture:
        return "unreviewed"
    state = attestation_fixture.get("review_state")
    if state in REVIEW_STATES:
        return state
    return "unreviewed"


def _evaluate_substrate_eligible(score_response: dict[str, Any]) -> tuple[bool, list[str]]:
    """Re-use the report-card eligibility evaluator to keep one rule path.

    Local import to avoid an import cycle when verdict_envelope is loaded
    during scoring engine startup.
    """
    from report_card.render import evaluate_bind_eligibility

    decision = evaluate_bind_eligibility(score_response)
    return bool(decision.get("eligible", False)), list(decision.get("reasons", []))


def _evaluate_attestation_eligible(
    attestation_subscore: float,
    pillar_floors: dict[str, Any],
    review_state: str,
    p4_5_raw: int | None,
) -> tuple[bool, list[str]]:
    """Attestation eligibility = above the P4 pillar floor AND not uw_rejected.

    Mirrors the substrate floor logic at the pillar level only — the
    attestation surface does not produce per-dim raws beyond the tier
    ladder, so the eligibility check is at the pillar aggregate. P4.5=0
    (no eval ledger) is bind-blocking per BEAAA-200 design lock; UW
    review cannot upgrade an absence.
    """
    reasons: list[str] = []
    gov_floor = float((pillar_floors.get("GOVERNANCE") or {}).get("min_pillar_score", 0.0))
    if review_state == "uw_rejected":
        reasons.append("attestation explicitly rejected by Underwriter (review_state == uw_rejected)")
    if attestation_subscore < gov_floor:
        reasons.append(
            f"P4 pillar {attestation_subscore:.2f} < {_floor_str(gov_floor)} floor"
            f" (BEAAA-13 sample; BEAAA-400 HoUW lock 2026-05-15)"
        )
    if p4_5_raw is not None and p4_5_raw <= 0:
        reasons.append(
            "P4.5 raw 0 — no eval ledger; bind-blocking gap that UW review cannot upgrade absence"
        )
    return (not reasons), reasons


def _resolve_bind_eligibility(
    substrate_eligible: bool,
    attestation_eligible: bool,
    review_state: str,
    applies_uw_guarantee_2: bool,
) -> str:
    """Compose the fused `bind_eligibility` enum per `min(substrate, attestation)`.

    Selection-rule deviation triggers HoUW per BEAAA-766 watchpoint #1.
    `review_required` is reserved for the case where both sides pass
    optimistically but the attestation is still pending UW review and the
    substrate is leaning on family-level priors (no live measurement on
    file yet). That combination cannot be auto-bound; UW eyes are needed
    even though no pillar floor is breached on the optimistic view.
    """
    if not substrate_eligible and not attestation_eligible:
        return "ineligible_substrate"
    if not substrate_eligible:
        return "ineligible_substrate"
    if not attestation_eligible:
        return "ineligible_attestation"
    if review_state == "unreviewed" and applies_uw_guarantee_2:
        return "review_required"
    return "eligible"


def _build_primary_reasons(
    bind_eligibility: str,
    *,
    attestation_subscore: float,
    pillar_floors: dict[str, Any],
    review_state: str,
    applies_uw_guarantee_2: bool,
    worst_case_gov: float,
    substrate_reasons: Iterable[str],
    attestation_reasons: Iterable[str],
) -> list[str]:
    """Order primary_reasons most-actionable-for-UW first.

    UW-actionable wins (attestation review can move the verdict); substrate
    worst-case reasons trail behind because closing them is post-Phase-1
    work (live measurement of priored dims). Order is a presentation
    recommendation per `are-scanner-api-v1.0.3-patch.md` §Semantics — the
    pricing engine MUST NOT depend on order.
    """
    out: list[str] = []
    gov_floor = float((pillar_floors.get("GOVERNANCE") or {}).get("min_pillar_score", 0.0))
    if bind_eligibility in ("ineligible_attestation", "ineligible_substrate", "review_required"):
        if review_state == "unreviewed" and attestation_subscore < gov_floor:
            out.append(
                f"attestation.review_state == unreviewed; "
                f"P4 pillar {attestation_subscore:.2f} < {_floor_str(gov_floor)} floor "
                f"(BEAAA-13 sample; BEAAA-400 HoUW lock 2026-05-15)"
            )
        if applies_uw_guarantee_2 and worst_case_gov == 0:
            out.append(
                "substrate.worst_case_loaded.governance_subscore == 0 "
                "(P4.1-P4.6 sourced from family-level prior; UW guarantee 2 worst-case loading applies)"
            )
    # Fall back to the raw reason lists when the ordered templates above
    # didn't fire (e.g. ineligible_substrate driven by P3 weak-link).
    if not out:
        out.extend(attestation_reasons)
        out.extend(substrate_reasons)
    return out


def compute_verdict_envelope(
    score_response: dict[str, Any],
    *,
    attestation_fixture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render the v1.0.3 fused `verdict` envelope.

    Parameters
    ----------
    score_response : dict
        The substrate-side ScoreResponse — must carry `bmi` (or `are_total`),
        `pillars` with per-dim `evidence_uri` + `raw`, and `pillar_floors`.
    attestation_fixture : dict | None
        Customer P4 attestation fixture (see
        `harness/fixtures/p4_attestation_pilot_v1.json` for the canonical
        AcmeBot v3 reference). When omitted, the attestation component
        defaults to `unreviewed` with zero claimed tiers — attestation is
        ineligible, which forces the fused verdict to `ineligible_attestation`
        unless the substrate side also fails.

    Returns
    -------
    dict
        `{"verdict": {...}}` — caller merges into the live ScoreResponse.
    """
    pillars = score_response.get("pillars", {}) or {}
    pillar_floors = score_response.get("pillar_floors", {}) or {}

    # Substrate side — optimistic-loaded (priors as-emitted in pillars).
    substrate_eligible, substrate_eligibility_reasons = _evaluate_substrate_eligible(score_response)
    substrate_are_total = int(score_response.get("bmi", score_response.get("are_total", 0)))
    substrate_gov_subscore = round(float(pillars.get("GOVERNANCE", {}).get("score", 0.0)), 4)

    # Substrate side — worst-case-loaded twin (priors collapsed per UW #2).
    worst_case = _compute_worst_case_loaded(pillars, pillar_floors)

    # Attestation side — review_state + BEAAA-214 Q3 cap applied mechanically.
    review_state = _resolve_review_state(attestation_fixture)
    attestation_subscore = _attestation_governance_subscore(
        attestation_fixture, review_state, pillars
    )
    p4_5_tier = _attestation_dim_tiers(attestation_fixture, review_state).get("P4.5")
    attestation_eligible, attestation_eligibility_reasons = _evaluate_attestation_eligible(
        attestation_subscore, pillar_floors, review_state, p4_5_tier
    )

    # Fuse: `min(substrate, attestation)` — HoUW BEAAA-766 ruling.
    bind_eligibility = _resolve_bind_eligibility(
        substrate_eligible,
        attestation_eligible,
        review_state,
        worst_case["applies_uw_guarantee_2"],
    )

    primary_reasons = _build_primary_reasons(
        bind_eligibility,
        attestation_subscore=attestation_subscore,
        pillar_floors=pillar_floors,
        review_state=review_state,
        applies_uw_guarantee_2=worst_case["applies_uw_guarantee_2"],
        worst_case_gov=worst_case["governance_subscore"],
        substrate_reasons=substrate_eligibility_reasons,
        attestation_reasons=attestation_eligibility_reasons,
    )

    substrate_block: dict[str, Any] = {
        "eligible": substrate_eligible,
        "are_total": substrate_are_total,
        "governance_subscore": substrate_gov_subscore,
        "worst_case_loaded": {
            "are_total": worst_case["are_total"],
            "governance_subscore": worst_case["governance_subscore"],
            "applies_uw_guarantee_2": worst_case["applies_uw_guarantee_2"],
            "rationale": worst_case["rationale"],
        },
        "reasons": list(substrate_eligibility_reasons),
    }

    attestation_block: dict[str, Any] = {
        "eligible": attestation_eligible,
        "are_total": None,
        "governance_subscore": attestation_subscore,
        "review_state": review_state,
        "reasons": list(attestation_eligibility_reasons),
    }

    return {
        "verdict": {
            "bind_eligibility": bind_eligibility,
            "selection_rule": SELECTION_RULE,
            "primary_reasons": primary_reasons,
            "components": {
                "substrate": substrate_block,
                "attestation": attestation_block,
            },
        }
    }


def attach_p4_evidence_metadata(
    evidence_refs: list[dict[str, Any]],
    *,
    attestation_fixture: dict[str, Any] | None,
    review_state: str | None = None,
) -> list[dict[str, Any]]:
    """Attach `review_state` + `tier` to every P4 evidence_refs[] row.

    v1.0.3 §Output promotion #4 (MUST-emit). Reads `review_state` off the
    attestation fixture (default unreviewed) and the per-dim tier off
    `_metadata.expected_form_claimed_tiers` after applying the
    raw_self_report cap when state is `unreviewed`. P4.5 has no claimed
    tier per the pilot fixture and surfaces as tier 0.
    """
    if review_state is None:
        review_state = _resolve_review_state(attestation_fixture)
    tiers = _attestation_dim_tiers(attestation_fixture, review_state)
    for row in evidence_refs:
        did = row.get("rubric_point_id", "")
        if not did.startswith("P4."):
            continue
        row["review_state"] = review_state
        row["tier"] = tiers.get(did, 0)
    return evidence_refs


__all__ = [
    "BIND_ELIGIBILITY",
    "REVIEW_STATES",
    "ROW_VERDICTS",
    "SELECTION_RULE",
    "compute_verdict_envelope",
    "attach_p4_evidence_metadata",
]
