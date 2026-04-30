"""Pricing v1 quote engine — applies BEAAA-10 pricing-v1 §1 formula.

Annual Premium = BaseRate × BMIBandFactor × ModifierFactor × SectorFactor
                 × RevenueFactor × LimitFactor × Aggregate

Endorsements are additive and computed separately (§8). Remediation is
auto-added for letter-C binds per §3.

Inputs: a ScoreResponse-shaped dict (from the scanner) + a Customer dict.
Output: a Quote-shaped dict with breakdown + status.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import yaml


class QuoteError(ValueError):
    pass


class ManualReferral(Exception):
    """Raised when an input forces manual underwriting (D band, oversize revenue/limit, OTHER sector)."""

    def __init__(self, reason: str, *, breakdown: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.breakdown = breakdown or {}


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


def quote(
    score: dict[str, Any],
    customer: dict[str, Any],
    *,
    rates: Rates,
    quoted_at: datetime | None = None,
) -> dict[str, Any]:
    """Apply pricing-v1 formula to a scanner result + customer profile."""
    letter, modifier_factor = _validate_score(score)
    sector, revenue_usd, per_claim, agg_limit, endorsement_codes = _validate_customer(customer)

    # v0.1.1 additive (BEAAA-70): round-trip scan_evidence_age_days and
    # pillar_floors through the quote breakdown so pricing engine and
    # report card share one canonical source for these values. Pricing
    # formula is unchanged in v0.1.1 — these are stamped, not consumed.
    age_days = _scan_evidence_age_days(score, quoted_at)
    pillar_floors = score.get("pillar_floors") if isinstance(score.get("pillar_floors"), dict) else None

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
    }

    # Manual-referral cases: D band, OTHER sector, oversize revenue/limit.
    if letter == "D":
        raise ManualReferral(
            "letter D — decline / layered-excess only; manual underwriting required",
            breakdown=breakdown,
        )

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
    ]
    annual_premium = factors[0]
    for f in factors[1:]:
        annual_premium = annual_premium * f
    annual_premium_usd = int(annual_premium.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    endorsements = _resolve_endorsements(rates, endorsement_codes, letter)
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
