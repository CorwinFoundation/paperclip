"""Pricing v1 quote engine — applies BEAAA-10 pricing-v1 §1 formula.

Annual Premium = BaseRate × AREBandFactor × ModifierFactor × SectorFactor
                 × RevenueFactor × LimitFactor × Aggregate × CaveatFactor

Endorsements are additive and computed separately (§8). Remediation is
auto-added for letter-C binds per §3. The CaveatFactor multiplier is added
by ADR-0007 §1.3 when the optional admittance_decision pre-condition gate
fires CONSTRAINED_BIND; legacy callers that omit admittance_decision get
CaveatFactor = 1.0 and a byte-identical breakdown.

Inputs: a ScoreResponse-shaped dict (from the scanner) + a Customer dict;
optionally an AdmittanceDecision (ADR-0007 §1.3) as a pre-condition gate.
Output: a Quote-shaped dict with breakdown + status.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable

import yaml


class QuoteError(ValueError):
    pass


class ManualReferral(Exception):
    """Raised when an input forces manual underwriting (D band, oversize revenue/limit, OTHER sector,
    or the ADR-0007 §1.3 admittance dial firing REFER_TO_UW per ADM.R4 / ADM.R6 / default)."""

    def __init__(self, reason: str, *, breakdown: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.breakdown = breakdown or {}


class Ineligible(Exception):
    """Raised when the ADR-0007 §1.3 admittance dial fires REFUSE.

    Carries the firing rule id and the full AdmittanceDecision as a dict so
    the upstream intake layer can populate the decline-letter template
    (rule-specific reason language is authored by Legal Coordinator on a
    separate ticket per ADR-0007 §6.3)."""

    def __init__(
        self,
        reason: str,
        *,
        rule_id: str,
        decision: dict[str, Any] | None = None,
        breakdown: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.rule_id = rule_id
        self.decision = decision or {}
        self.breakdown = breakdown or {}


# ADR-0007 §2 telemetry sink. Module-global so callers can install a single
# dashboard writer once at process start; tests inject a list-collector.
# The sink receives one event dict per quote attempt whenever an admittance
# decision is supplied; production wiring to the UW dashboard surface is
# the deployment's responsibility, kept out of this pure pricing module.
_TELEMETRY_SINK: Callable[[dict[str, Any]], None] | None = None


def set_telemetry_sink(sink: Callable[[dict[str, Any]], None] | None) -> None:
    """Install (or clear with None) the ADR-0007 §2 telemetry sink."""
    global _TELEMETRY_SINK
    _TELEMETRY_SINK = sink


def _emit_telemetry(event: dict[str, Any]) -> None:
    sink = _TELEMETRY_SINK
    if sink is None:
        return
    try:
        sink(event)
    except Exception:
        # Telemetry MUST NOT break the quote path. A broken dashboard sink
        # is a dashboard problem, not a binding-eligibility problem.
        pass


@dataclass(frozen=True)
class Rates:
    version: str
    frozen_at: str
    base_rate_usd: int
    aggregate: float
    band_factor: dict[str, float]
    sector_factor: dict[str, float]
    revenue_bands: list[dict[str, Any]]
    limit_tiers: list[dict[str, Any]]
    endorsements: dict[str, dict[str, Any]]
    live_probe: dict[str, Any] | None = None

    @classmethod
    def load(cls, path: str | Path) -> "Rates":
        data = yaml.safe_load(Path(path).read_text())
        return cls(
            version=data["version"],
            frozen_at=data["frozen_at"],
            base_rate_usd=int(data["base_rate_usd"]),
            aggregate=float(data["aggregate"]),
            band_factor={k: float(v) for k, v in data["band_factor"].items()},
            sector_factor={k: float(v) for k, v in data["sector_factor"].items()},
            revenue_bands=list(data["revenue_bands"]),
            limit_tiers=list(data["limit_tiers"]),
            endorsements=dict(data["endorsements"]),
            live_probe=dict(data["live_probe"]) if data.get("live_probe") else None,
        )


def _sector_factor(rates: Rates, sector: str) -> float:
    if sector == "OTHER":
        raise ManualReferral("sector OTHER routes to manual underwriting")
    if sector not in rates.sector_factor:
        raise QuoteError(f"unknown sector {sector!r}; not in pricing-v1 §4 taxonomy")
    return rates.sector_factor[sector]


def _revenue_factor(rates: Rates, revenue_usd: int) -> tuple[float, str]:
    if revenue_usd < 0:
        raise QuoteError(f"annual_revenue_usd must be >= 0, got {revenue_usd}")
    for band in rates.revenue_bands:
        min_usd = int(band["min_usd"])
        max_excl = band["max_usd_excl"]
        if revenue_usd < min_usd:
            continue
        if max_excl is not None and revenue_usd >= int(max_excl):
            continue
        if band["factor"] is None:
            raise ManualReferral(f"revenue band {band['label']} routes to manual")
        return float(band["factor"]), band["label"]
    raise ManualReferral(f"revenue ${revenue_usd:,} not in any band; manual referral")


def _limit_factor(rates: Rates, per_claim_usd: int, aggregate_usd: int) -> float:
    for tier in rates.limit_tiers:
        if tier["per_claim_usd"] == per_claim_usd and tier["aggregate_usd"] == aggregate_usd:
            return float(tier["factor"])
    raise ManualReferral(
        f"limit ${per_claim_usd:,}/${aggregate_usd:,} not in pricing-v1 §6 tiers; manual referral"
    )


def _band_factor(rates: Rates, letter: str) -> float:
    if letter not in rates.band_factor:
        raise QuoteError(f"unknown band letter {letter!r}")
    return rates.band_factor[letter]


def _validate_score(score: dict[str, Any]) -> tuple[str, float]:
    band = score.get("band")
    if not isinstance(band, dict):
        raise QuoteError("score.band missing or not an object")
    letter = band.get("letter")
    factor = band.get("modifier_factor")
    if letter not in ("A", "B", "C", "D"):
        raise QuoteError(f"score.band.letter must be A/B/C/D, got {letter!r}")
    if not isinstance(factor, (int, float)):
        raise QuoteError("score.band.modifier_factor missing")
    return letter, float(factor)


def _validate_customer(customer: dict[str, Any]) -> tuple[str, int, int, int, list[str]]:
    for field in ("sector", "annual_revenue_usd", "limit"):
        if field not in customer:
            raise QuoteError(f"customer.{field} required")
    limit = customer["limit"]
    if not isinstance(limit, dict):
        raise QuoteError("customer.limit must be an object")
    for field in ("per_claim_usd", "aggregate_usd"):
        if field not in limit:
            raise QuoteError(f"customer.limit.{field} required")
    endorsements = customer.get("endorsements", []) or []
    if not isinstance(endorsements, list):
        raise QuoteError("customer.endorsements must be a list of strings")
    return (
        str(customer["sector"]),
        int(customer["annual_revenue_usd"]),
        int(limit["per_claim_usd"]),
        int(limit["aggregate_usd"]),
        [str(e) for e in endorsements],
    )


def _resolve_endorsements(rates: Rates, requested: list[str], letter: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    if letter == "C" and "remediation" not in requested:
        requested = list(requested) + ["remediation"]
    for code in requested:
        if code in seen:
            continue
        seen.add(code)
        if code not in rates.endorsements:
            raise QuoteError(f"unknown endorsement {code!r}; not in pricing-v1 §8")
        spec = rates.endorsements[code]
        out.append({
            "code": code,
            "load_usd": int(spec["load_usd"]),
            "description": str(spec.get("description", "")),
            "auto_added": (code == "remediation" and letter == "C"),
        })
    return out


def _piecewise_load(spread: float, breakpoints: list[dict[str, Any]]) -> float:
    """Linear interpolation of factor between named (spread, factor) breakpoints.

    Below the first breakpoint spread → first breakpoint factor (floor).
    Above the last breakpoint spread → last breakpoint factor (cap before
    refuse-to-quote gate; refuse is enforced separately by caller).
    """
    if not breakpoints:
        return 1.0
    if spread <= float(breakpoints[0]["spread"]):
        return float(breakpoints[0]["factor"])
    for i in range(1, len(breakpoints)):
        prev = breakpoints[i - 1]
        cur = breakpoints[i]
        if spread <= float(cur["spread"]):
            x0, y0 = float(prev["spread"]), float(prev["factor"])
            x1, y1 = float(cur["spread"]), float(cur["factor"])
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (spread - x0) / (x1 - x0)
    return float(breakpoints[-1]["factor"])


def _pillar_of(rubric_point_id: str) -> str:
    """Extract pillar prefix from a rubric_point_id (e.g. 'P3.1' → 'P3').

    Convention is locked by rubric.yaml: dim ids are 'P<n>.<m>' where n in
    {1,2,3,4} corresponds to {OPS, BIAS_FAIRNESS, ROBUSTNESS, GOVERNANCE}.
    """
    return rubric_point_id.split(".", 1)[0] if "." in rubric_point_id else rubric_point_id


def _apply_live_probe_loading(
    score: dict[str, Any],
    live_probe_rates: dict[str, Any] | None,
) -> tuple[float, dict[str, Any] | None]:
    """Compute the live_probe spread-load multiplier per rate-book v1.0 (BEAAA-536).

    Returns (factor, detail_dict). Factor is 1.0 for any non-live_probe path
    so offline_replay quotes are byte-identical pre/post this addendum.

    Refuse-to-quote conditions raise ManualReferral:
      - incomplete: true on the score payload (mirrors UW Hard Guarantee #1)
      - any dim spread above its pillar-criticality threshold
    """
    scan_mode = score.get("scan_mode")
    if scan_mode != "live_probe":
        return 1.0, None
    if live_probe_rates is None:
        raise QuoteError("rates.yaml is missing the live_probe block required for scan_mode='live_probe'")

    if score.get("incomplete") is True:
        raise ManualReferral(
            "live_probe scan incomplete (spread band exceeded or K-replicates short); refuse-to-quote per UW #1",
        )

    spread_per_dim = score.get("spread_per_dim")
    if not isinstance(spread_per_dim, dict) or not spread_per_dim:
        raise QuoteError("scan_mode='live_probe' requires score.spread_per_dim (non-empty object)")

    refuse = live_probe_rates["refuse_to_quote_per_dim"]
    critical = set(refuse["critical_pillars"])
    hygiene = set(refuse["hygiene_pillars"])
    crit_threshold = float(refuse["critical_threshold"])
    hyg_threshold = float(refuse["hygiene_threshold"])
    breakpoints = list(live_probe_rates["loading_curve"]["breakpoints"])
    cap = float(live_probe_rates["loading_curve"]["cap"])
    aggregation = live_probe_rates["loading_curve"].get("aggregation", "max")

    per_dim_factors: dict[str, float] = {}
    for dim_id, raw_spread in spread_per_dim.items():
        try:
            spread = float(raw_spread)
        except (TypeError, ValueError) as exc:
            raise QuoteError(f"spread_per_dim[{dim_id!r}] must be numeric, got {raw_spread!r}") from exc
        if spread < 0:
            raise QuoteError(f"spread_per_dim[{dim_id!r}] must be >= 0, got {spread}")
        pillar = _pillar_of(dim_id)
        if pillar in critical:
            threshold = crit_threshold
        elif pillar in hygiene:
            threshold = hyg_threshold
        else:
            threshold = crit_threshold  # unknown-pillar dims default to the tighter band
        if spread > threshold:
            raise ManualReferral(
                f"live_probe per-dim refuse-to-quote: {dim_id} spread {spread:.2f} > threshold {threshold:.2f} "
                f"(pillar {pillar}, {'critical' if pillar in critical else 'hygiene'})",
            )
        per_dim_factors[dim_id] = _piecewise_load(spread, breakpoints)

    if aggregation == "max":
        aggregated = max(per_dim_factors.values()) if per_dim_factors else 1.0
    elif aggregation == "product":
        aggregated = 1.0
        for f in per_dim_factors.values():
            aggregated *= f
    else:
        raise QuoteError(f"unknown live_probe aggregation {aggregation!r}; expected 'max' or 'product'")
    aggregated = min(aggregated, cap)

    return aggregated, {
        "policy_version": live_probe_rates.get("policy_version"),
        "aggregation": aggregation,
        "cap": cap,
        "spread_load_factor": aggregated,
        "per_dim_factor_max_dim": max(per_dim_factors, key=per_dim_factors.get) if per_dim_factors else None,
        "per_dim_factor_max_value": max(per_dim_factors.values()) if per_dim_factors else 1.0,
        "k_replicates_observed": score.get("k_replicates_observed"),
        "live_probe_variance_band": score.get("live_probe_variance_band"),
    }


def _check_k_replicate_policy(
    score: dict[str, Any],
    customer: dict[str, Any],
    live_probe_rates: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Soft warning when a mandatory K=10 condition fires but caller ran K=5.

    Does not refuse-to-quote — the spread-band gates handle the substantive
    failure mode. Pure advisory: stamped onto the breakdown for audit.
    """
    if score.get("scan_mode") != "live_probe" or live_probe_rates is None:
        return None
    policy = live_probe_rates.get("k_replicate_selection", {})
    conditions: list[str] = []
    revenue_usd = int(customer.get("annual_revenue_usd", 0) or 0)
    if revenue_usd >= 50_000_000 and "revenue_tier_gte_t3" in policy.get("mandatory_k10_conditions", []):
        conditions.append("revenue_tier_gte_t3")
    if customer.get("prior_midterm_rescan_trigger") and "prior_midterm_rescan_trigger" in policy.get("mandatory_k10_conditions", []):
        conditions.append("prior_midterm_rescan_trigger")
    if customer.get("model_change_within_term") and "customer_attests_model_change_in_term" in policy.get("mandatory_k10_conditions", []):
        conditions.append("customer_attests_model_change_in_term")
    observed_k = score.get("k_replicates_observed")
    required_k = 10 if conditions else int(policy.get("default_k", 5))
    advisory = None
    if conditions and observed_k is not None and int(observed_k) < 10:
        advisory = (
            f"K=10 mandatory per conditions {conditions} but observed K={observed_k}; "
            "advisory only (spread gates already enforced)"
        )
    return {
        "required_k": required_k,
        "observed_k": observed_k,
        "triggered_conditions": conditions,
        "advisory": advisory,
    }


def _scan_evidence_age_days(score: dict[str, Any], quoted_at: datetime | None) -> int | None:
    """Recompute v0.1.1 scan_evidence_age_days from score.scored_at at quote time.

    Returns None when scored_at is missing or unparseable (older Scanner emit
    that pre-dates v0.1.1). Floors at 0 so an unsynced clock cannot record a
    negative age that would silently bypass the 90-day stale-scan trip-wire.
    """
    scored_at_str = score.get("scored_at")
    if not isinstance(scored_at_str, str) or not scored_at_str:
        return None
    try:
        scored_at = datetime.fromisoformat(scored_at_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if scored_at.tzinfo is None:
        scored_at = scored_at.replace(tzinfo=timezone.utc)
    now = quoted_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = now - scored_at
    return max(0, delta.days)


def _decision_as_dict(decision: Any) -> dict[str, Any]:
    """Normalize either an AdmittanceDecision dataclass or a dict to a dict.

    Keeps `pricing` import-free of the `underwriting` package so callers
    can pass either shape. The CTO post-processor returns the dataclass;
    serialized intake records carry the dict.
    """
    if decision is None:
        raise QuoteError("admittance_decision required for the new admittance-gated path")
    if isinstance(decision, dict):
        return dict(decision)
    to_dict = getattr(decision, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    from dataclasses import asdict, is_dataclass

    if is_dataclass(decision):
        return asdict(decision)
    raise QuoteError(f"admittance_decision shape not recognized: {type(decision).__name__}")


def _na_dims_from_score(score: dict[str, Any]) -> list[str]:
    """Extract na_dims list from either the applicability_record envelope
    or the coverage_caveat diagnostic detail (ADR-0007 §1.2 evidence bundle)."""
    uncovered = score.get("uncovered_dimensions")
    if isinstance(uncovered, list) and uncovered:
        out: list[str] = []
        for item in uncovered:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                dim_id = item.get("dim_id") or item.get("rubric_point_id")
                if isinstance(dim_id, str):
                    out.append(dim_id)
        if out:
            return out
    app = score.get("applicability_record") or score.get("applicability") or {}
    if isinstance(app, dict):
        na = app.get("na_dims")
        if isinstance(na, list):
            return [str(d) for d in na]
    for d in score.get("diagnostics", []) or []:
        if isinstance(d, dict) and d.get("code") == "coverage_caveat":
            detail = d.get("detail") or {}
            na = detail.get("na_dims") if isinstance(detail, dict) else None
            if isinstance(na, list):
                return [str(x) for x in na]
    return []


def _coverage_caveat_endorsement(na_dims: list[str], *, is_exclusion: bool) -> dict[str, Any]:
    """Build the synthetic coverage-caveat endorsement row.

    Two shapes per ADR-0007 §1.3:
    - CONSTRAINED_BIND → is_exclusion=True: exclusion endorsement, wording
      authored by Legal Coordinator under ADR-0007 §6.3. This row carries
      load_usd=0 because the dollar effect is the f_caveat premium uplift
      applied in the rated-factor chain, not an additive load.
    - AUTO_BIND → is_exclusion=False: transparency row enumerating the
      na_dims on the policy schedule, no exclusion, no uplift.
    """
    if is_exclusion:
        code = "coverage_caveat_exclusion"
        description = (
            f"Coverage-caveat exclusion endorsement (ADR-0007 §1.3). "
            f"Excludes losses arising from peril surfaces of na_dims "
            f"({', '.join(na_dims) if na_dims else 'none'}). "
            f"Wording authored by Legal Coordinator policy-wording cycle "
            f"(ADR-0007 §6.3)."
        )
    else:
        code = "coverage_caveat_transparency"
        description = (
            f"Coverage-caveat block (ADR-0007 §1.3 AUTO_BIND row): policy "
            f"schedule discloses N/A dims "
            f"({', '.join(na_dims) if na_dims else 'none'}) without exclusion."
        )
    return {
        "code": code,
        "load_usd": 0,
        "description": description,
        "auto_added": True,
        "na_dims": list(na_dims),
        "adr_ref": "docs/adr-0007-option-c-admittance-dial.md",
    }


def _misattribution_exclusion_endorsement() -> dict[str, Any]:
    """ADR-0053 (BEAAA-12052) Condition 2 — interim Misattribution-of-Value &
    Derived-Figure Exclusion (uw-fact-consumption-gate-v0-1 §5, HUW-authored).

    Attached to any full-limit business-factual bind whose eligibility rests on a
    v1 (`verifier_version < 2`) `deployment_measured` tool-grounded-factuality row.
    The v1 verifier grounds on SET MEMBERSHIP: it catches INVENTED numbers but not
    MISATTRIBUTED ones (a real value bound to the wrong entity) or ARITHMETIC-derived
    figures. The exposure is written full-limit for invented-value factuality and
    excluded for those two defined perils until a v2 verifier closes per-claim
    value↔entity binding, at which point it sunsets automatically. load_usd=0: this
    narrows coverage, it is not an additive premium load.
    """
    return {
        "code": "misattribution_derived_figure_exclusion",
        "load_usd": 0,
        "description": (
            "Misattribution-of-Value & Derived-Figure Exclusion (Business-Factual) "
            "(ADR-0053 / uw-fact-consumption-gate-v0-1 §5). Applies where full-limit "
            "business-factual eligibility rests on a v1 deployment_measured "
            "tool-grounded-factuality row. Excludes loss arising from a "
            "misattribution-of-value — a numeric statement of fact individually "
            "present in a tool-returned value or user turn but bound to the wrong "
            "entity, record, or reference — and loss arising from an "
            "arithmetic-derived figure computed or aggregated by the agent rather "
            "than returned verbatim by a tool or user turn. Invented-value "
            "factuality (which the v1 verifier tests) is written full-limit. "
            "Sunsets automatically for rows produced at verifier_version >= 2."
        ),
        "auto_added": True,
        "adr_ref": "docs/adr/adr-0053-beaaa12052-fact-consumption-gate.md",
    }


def _f_caveat(na_dims_count: int) -> float:
    """ADR-0007 §1.3 placeholder uplift: f_caveat = 1 + 0.05 × len(na_dims).

    DO NOT calibrate this unilaterally. Calibration after the first 30
    production CONSTRAINED_BIND quotes routes through HUW rule-change call
    per `contracts/uw-rubric-supervision-v1.md@v1.0` row 30 (ADR §6.1).
    """
    return 1.0 + 0.05 * max(0, int(na_dims_count))


def _telemetry_event_for(
    decision: dict[str, Any],
    *,
    score: dict[str, Any],
    customer: dict[str, Any],
    quoted_at: datetime | None,
) -> dict[str, Any]:
    """Build the ADR-0007 §2 telemetry event for one quote attempt.

    Includes the three §4 trigger watch flags so the dashboard can roll up
    rolling counters without re-deriving them:
      - trigger_2_watch: ADM.R2 fired (pillars_with_all_na ≥ 1)
      - trigger_3_watch: ADM.R4 fired (posture=none AND are_total ≥ 50)
      - trigger_4_watch: hardened-posture submission fired REFUSE on R2/R3
    """
    rule_id = decision.get("firing_rule_id") or decision.get("rule_id") or "unknown"
    klass = decision.get("admittance_class") or "unknown"
    sys_kind = decision.get("system_prompt_kind")
    trigger_4 = (
        sys_kind == "hardened"
        and klass == "REFUSE"
        and rule_id in ("ADM.R2", "ADM.R3")
    )
    return {
        "event": "admittance_decision",
        "admittance_class": klass,
        "firing_rule_id": rule_id,
        "are_total": decision.get("are_total"),
        "system_prompt_kind": sys_kind,
        "measured_dims_count": decision.get("measured_dims_count"),
        "na_dims_count": decision.get("na_dims_count"),
        "pillars_with_all_na_count": decision.get("pillars_with_all_na_count"),
        "trigger_2_watch": rule_id == "ADM.R2",
        "trigger_3_watch": rule_id == "ADM.R4",
        "trigger_4_watch": trigger_4,
        "customer_sector": customer.get("sector"),
        "quoted_at": (quoted_at or datetime.now(timezone.utc)).isoformat(),
        "adr_ref": "docs/adr-0007-option-c-admittance-dial.md",
    }


def quote(
    score: dict[str, Any],
    customer: dict[str, Any],
    *,
    rates: Rates,
    quoted_at: datetime | None = None,
    admittance_decision: Any = None,
) -> dict[str, Any]:
    """Apply pricing-v1 formula to a scanner result + customer profile.

    When `admittance_decision` is provided (an `underwriting.admittance.AdmittanceDecision`
    or equivalent dict), enforce the ADR-0007 §1.3 admittance gate as a
    discrete pre-condition before the rated path runs:
      - REFUSE → raise Ineligible (no premium produced; decline-letter routed)
      - REFER_TO_UW → raise ManualReferral (full ARE evidence bundle in breakdown)
      - CONSTRAINED_BIND → rated path + f_caveat uplift + exclusion endorsement
      - AUTO_BIND → standard rated path + transparency coverage-caveat row

    When `admittance_decision` is None, legacy behavior is preserved (the
    pre-ADR-0007 D-band ManualReferral remains the only pre-condition gate).
    """
    letter, modifier_factor = _validate_score(score)
    sector, revenue_usd, per_claim, agg_limit, endorsement_codes = _validate_customer(customer)

    # ADR-0007 §1.3 admittance pre-condition gate. Runs after structural
    # validation so QuoteError still fires for malformed input regardless
    # of admittance class.
    decision_dict: dict[str, Any] | None = None
    klass: str | None = None
    if admittance_decision is not None:
        decision_dict = _decision_as_dict(admittance_decision)
        klass = decision_dict.get("admittance_class")
        rule_id = decision_dict.get("firing_rule_id") or decision_dict.get("rule_id") or "unknown"
        _emit_telemetry(
            _telemetry_event_for(
                decision_dict, score=score, customer=customer, quoted_at=quoted_at
            )
        )
        if klass == "REFUSE":
            raise Ineligible(
                f"admittance class REFUSE — rule {rule_id} fired; no premium produced "
                f"(ADR-0007 §1.3). Decline-letter wording routed via Legal Coordinator.",
                rule_id=rule_id,
                decision=decision_dict,
                breakdown={"admittance": decision_dict, "rates_version": rates.version},
            )
        if klass == "REFER_TO_UW":
            evidence = {
                "are_total": decision_dict.get("are_total"),
                "system_prompt_kind": decision_dict.get("system_prompt_kind"),
                "measured_dims": (score.get("applicability_record") or {}).get("measured_dims"),
                "na_dims": _na_dims_from_score(score),
                "coverage_caveat": next(
                    (d for d in (score.get("diagnostics") or []) if isinstance(d, dict) and d.get("code") == "coverage_caveat"),
                    None,
                ),
                "subscores": score.get("subscores"),
                "applicability_record": score.get("applicability_record"),
            }
            raise ManualReferral(
                f"admittance class REFER_TO_UW — rule {rule_id} fired; route to manual UW queue (ADR-0007 §1.3).",
                breakdown={"admittance": decision_dict, "evidence": evidence, "rates_version": rates.version},
            )
        # F2 / REV-002 (BEAAA-14398): fail-closed default on the money path.
        # REFUSE and REFER_TO_UW already raised above; the only classes that may
        # continue to the rated path are CONSTRAINED_BIND and AUTO_BIND. Any other
        # value present alongside a decision (lowercase 'refuse', a typo, a
        # corrupted intake, or None-with-decision-present) previously fell through
        # to a full-limit rated quote with f_caveat=1.0 — a money-path fail-open.
        # Fail closed to a human instead. We do NOT normalize case (render.py:980
        # uppercases for display only); an unrecognized class is referred, never
        # guessed into REFUSE/Ineligible.
        if klass not in ("CONSTRAINED_BIND", "AUTO_BIND"):
            raise ManualReferral(
                f"admittance class {klass!r} not recognized — rule {rule_id}; "
                f"fail-closed to manual underwriting, no premium produced (F2 / ADR-0007 §1.3).",
                breakdown={"admittance": decision_dict, "rates_version": rates.version},
            )

    # v0.1.1 additive (BEAAA-70): round-trip scan_evidence_age_days and
    # pillar_floors through the quote breakdown so pricing engine and
    # report card share one canonical source for these values. Pricing
    # formula is unchanged in v0.1.1 — these are stamped, not consumed.
    age_days = _scan_evidence_age_days(score, quoted_at)
    pillar_floors = score.get("pillar_floors") if isinstance(score.get("pillar_floors"), dict) else None

    scan_mode = score.get("scan_mode", "offline_replay")
    breakdown: dict[str, Any] = {
        "base_rate_usd": rates.base_rate_usd,
        "band_letter": letter,
        "band_factor": _band_factor(rates, letter),
        "modifier": score["band"].get("modifier", "neutral"),
        "modifier_factor": modifier_factor,
        "sector": sector,
        "rates_version": rates.version,
        "scan_evidence_age_days": age_days,
        "pillar_floors": pillar_floors,
        "scan_mode": scan_mode,
    }

    # Manual-referral cases: D band, OTHER sector, oversize revenue/limit.
    if letter == "D":
        raise ManualReferral(
            "letter D — decline / layered-excess only; manual underwriting required",
            breakdown=breakdown,
        )

    # live_probe spread-load + per-dim refuse-to-quote (BEAAA-536 rate-book v1.0).
    # offline_replay path: returns 1.0 with no breakdown entry → byte-identical.
    live_probe_factor, live_probe_detail = _apply_live_probe_loading(score, rates.live_probe)
    k_policy = _check_k_replicate_policy(score, customer, rates.live_probe)

    sector_factor = _sector_factor(rates, sector)
    revenue_factor, revenue_label = _revenue_factor(rates, revenue_usd)
    limit_factor = _limit_factor(rates, per_claim, agg_limit)

    breakdown.update({
        "sector_factor": sector_factor,
        "revenue_band": revenue_label,
        "revenue_factor": revenue_factor,
        "limit": {"per_claim_usd": per_claim, "aggregate_usd": agg_limit, "factor": limit_factor},
        "aggregate": rates.aggregate,
    })
    if live_probe_detail is not None:
        breakdown["live_probe_spread_load"] = live_probe_detail
    if k_policy is not None:
        breakdown["k_replicate_policy"] = k_policy

    # ADR-0007 §1.3 f_caveat uplift — applies only on CONSTRAINED_BIND.
    # AUTO_BIND has no uplift; the no-admittance legacy path has no uplift.
    # f_caveat = 1.0 on every non-CONSTRAINED_BIND path keeps the pricing
    # formula byte-identical for the doc stress profiles (BEAAA-10 §9).
    f_caveat = 1.0
    na_dims_for_endorsement: list[str] = []
    if klass == "CONSTRAINED_BIND" and decision_dict is not None:
        na_dims_for_endorsement = _na_dims_from_score(score)
        na_count = decision_dict.get("na_dims_count")
        if not isinstance(na_count, int):
            na_count = len(na_dims_for_endorsement)
        f_caveat = _f_caveat(na_count)
        breakdown["admittance"] = decision_dict
        breakdown["f_caveat"] = f_caveat
        breakdown["f_caveat_na_dims_count"] = na_count
    elif klass == "AUTO_BIND" and decision_dict is not None:
        na_dims_for_endorsement = _na_dims_from_score(score)
        breakdown["admittance"] = decision_dict
        breakdown["f_caveat"] = 1.0

    # Decimal arithmetic with ROUND_HALF_UP so dollar figures match the
    # pricing-v1 §9 hand-computed worked examples (no float drift).
    factors = [
        Decimal(str(rates.base_rate_usd)),
        Decimal(str(breakdown["band_factor"])),
        Decimal(str(modifier_factor)),
        Decimal(str(sector_factor)),
        Decimal(str(revenue_factor)),
        Decimal(str(limit_factor)),
        Decimal(str(rates.aggregate)),
        Decimal(str(live_probe_factor)),
        Decimal(str(f_caveat)),
    ]
    annual_premium = factors[0]
    for f in factors[1:]:
        annual_premium = annual_premium * f
    annual_premium_usd = int(annual_premium.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    endorsements = _resolve_endorsements(rates, endorsement_codes, letter)
    # ADR-0007 §1.3 synthetic coverage-caveat endorsement row. CONSTRAINED_BIND
    # gets the exclusion shape (wording deferred to Legal Coordinator per ADR
    # §6.3, load_usd=0 because the dollar impact is f_caveat in the rated
    # chain); AUTO_BIND gets a transparency row with no exclusion.
    if klass == "CONSTRAINED_BIND":
        endorsements.append(_coverage_caveat_endorsement(na_dims_for_endorsement, is_exclusion=True))
    elif klass == "AUTO_BIND":
        endorsements.append(_coverage_caveat_endorsement(na_dims_for_endorsement, is_exclusion=False))
    # ADR-0053 (BEAAA-12052) Condition 2 — attach the interim misattribution /
    # derived-figure exclusion when a full-limit business-factual bind rests on a
    # v1 deployment_measured tool-grounded-factuality row (admittance carries the
    # flag). Off every non-bind path and off every submission without such a row,
    # so pricing for the golden roster and legacy stress profiles is unchanged.
    if (
        klass in ("CONSTRAINED_BIND", "AUTO_BIND")
        and decision_dict is not None
        and decision_dict.get("business_factual_v1_exclusion_required") is True
    ):
        endorsements.append(_misattribution_exclusion_endorsement())
    endorsements_total = sum(e["load_usd"] for e in endorsements)

    return {
        "annual_premium_usd": annual_premium_usd,
        "endorsements": endorsements,
        "endorsements_total_usd": endorsements_total,
        "total_with_endorsements_usd": annual_premium_usd + endorsements_total,
        "currency": "USD",
        "status": "indicative",
        "breakdown": breakdown,
        "pricing_version": rates.version,
    }
