"""Run the BMI Scanner against directional priors for real public AI agents.

Output: a Markdown leaderboard showing BMI / band / indicative quote against
a representative customer profile (ENT_SAAS, $100M revenue, $1M/$2M limits).

These are PRIORS, not hosted-eval measurements. See scripts/agent_priors/README.md.

Usage:
    python3 scripts/score_real_agents.py
    python3 scripts/score_real_agents.py --customer-revenue 1000000000
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow `python3 scripts/score_real_agents.py` from repo root without PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pricing.quote import ManualReferral, Rates, quote  # noqa: E402
from rubric.scoring import Rubric, score  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
RATES_PATH = REPO / "pricing" / "rates.yaml"
PRIORS_DIR = REPO / "scripts" / "agent_priors"


def _eval_outputs_from_priors(priors: dict, evidence_uri: str) -> dict:
    return {
        dim_id: {"raw": float(raw), "evidence_uri": evidence_uri}
        for dim_id, raw in priors["raw_scores"].items()
    }


def _representative_customer(annual_revenue_usd: int) -> dict:
    return {
        "sector": "ENT_SAAS",
        "annual_revenue_usd": annual_revenue_usd,
        "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
    }


def _format_premium(quote_or_referral) -> str:
    if isinstance(quote_or_referral, ManualReferral):
        return f"manual referral — {quote_or_referral.reason}"
    annual = quote_or_referral["annual_premium_usd"]
    total = quote_or_referral["total_with_endorsements_usd"]
    if total != annual:
        return f"${annual:,} (+${total - annual:,} endorsements = ${total:,})"
    return f"${annual:,}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customer-revenue", type=int, default=100_000_000,
                        help="Representative customer annual revenue in USD (default $100M).")
    args = parser.parse_args()

    rubric = Rubric.load(RUBRIC_PATH)
    rates = Rates.load(RATES_PATH)
    customer = _representative_customer(args.customer_revenue)
    scored_at = datetime(2026, 4, 29, tzinfo=timezone.utc)

    rows = []
    for prior_path in sorted(PRIORS_DIR.glob("*.json")):
        priors = json.loads(prior_path.read_text())
        evidence_uri = "https://priors.beaai.local/" + priors["id"]
        eval_outputs = _eval_outputs_from_priors(priors, evidence_uri)
        result = score(
            rubric, eval_outputs,
            snapshot_hash="sha256:prior-not-a-real-snapshot",
            forensic_bundle_uri="https://priors.beaai.local/no-bundle",
            scored_at=scored_at,
        )
        try:
            q = quote(result, customer, rates=rates)
            quote_str = _format_premium(q)
        except ManualReferral as exc:
            quote_str = _format_premium(exc)
        rows.append({
            "agent": priors["display_name"],
            "bmi": result["bmi"],
            "band": result["band"]["display"],
            "posture": result["band"]["posture"],
            "quote": quote_str,
            "rationale": priors["rationale"],
            "evidence_links": priors["evidence_links"],
        })

    rows.sort(key=lambda r: -r["bmi"])

    print("# Directional BMI leaderboard — real AI agents (priors, not measurements)")
    print()
    print(f"_Customer profile: ENT_SAAS, ${args.customer_revenue:,} annual revenue, $1M/$2M limits._")
    print()
    print("| # | Agent | BMI | Band | Indicative annual premium | Posture |")
    print("|--:|-------|----:|:----:|:--------------------------|---------|")
    for i, r in enumerate(rows, 1):
        print(f"| {i} | {r['agent']} | **{r['bmi']}** | **{r['band']}** | {r['quote']} | {r['posture']} |")
    print()
    print("## Per-agent rationale + evidence")
    print()
    for r in rows:
        print(f"### {r['agent']} — BMI {r['bmi']} ({r['band']})")
        print()
        print(r["rationale"])
        print()
        print("Evidence (public sources):")
        for link in r["evidence_links"]:
            print(f"- {link}")
        print()
    print("## Caveats")
    print()
    print("- Every score above is a directional **prior** based on publicly observable posture.")
    print("- Real BMI scoring requires the hosted-eval-only intake guard (BEAAA-22 §3): a snapshot hash + forensic evidence bundle + per-dim runs from the BEAAI-hosted eval harness.")
    print("- These priors should be used to prioritize pilot targets, not for binding, pricing, or any contract decision.")
    print("- Premium figures are indicative under pricing-v1 v1.0; pre-loss-data and explicitly placeholder.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
