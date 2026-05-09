"""BEAAA-130 demo run — ARE Scanner v0.1 against Pi.ai (Inflection AI).

Single-pass priors-based scan with phase-by-phase wall-clock timing so the
board can see the process, time, and current outputs end-to-end. No live
HTTP calls (Pi.ai blocks scrapers with 403); this is a directional priors
run, the same pipeline used for the rest of the public-agent leaderboard.

Outputs:
  - artifacts/beaaa-130/scan_pi_ai_trace.txt        — phase timing + key fields
  - artifacts/beaaa-130/scan_pi_ai_eval_outputs.json — frozen eval_outputs
  - artifacts/beaaa-130/scan_pi_ai_score.json       — raw score response
  - artifacts/beaaa-130/scan_pi_ai_quote.json       — indicative quote (or referral)
  - artifacts/beaaa-130/scan_pi_ai_card.html        — board-grade Report Card
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pricing.quote import ManualReferral, Rates, quote
from report_card.render import evaluate_bind_eligibility, render
from rubric.scoring import Rubric, score

PRIOR_PATH = REPO / "scripts" / "agent_priors" / "pi_ai.json"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
RATES_PATH = REPO / "pricing" / "rates.yaml"
OUT_DIR = REPO / "artifacts" / "beaaa-130"


def _phase(t0: float, label: str, lines: list[str]) -> float:
    now = time.perf_counter()
    elapsed_ms = (now - t0) * 1000
    line = f"  t+{elapsed_ms:7.1f}ms   {label}"
    print(line, flush=True)
    lines.append(line)
    return now


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    trace: list[str] = []
    header = [
        "ARE Scanner v0.1 — BEAAA-130 demo run",
        f"  target:        Pi (Inflection AI) — pi.ai consumer chat",
        f"  scan mode:     directional priors (no live HTTP — pi.ai blocks scrapers)",
        f"  rubric:        {RUBRIC_PATH.name} (4 pillars, 21 active dims, weights 35/20/30/15)",
        f"  rates:         {RATES_PATH.name} (pricing-v1)",
        f"  customer:      ENT_SAAS, $100M annual revenue, $1M/$2M limits",
        f"  scored_at:     {datetime.now(timezone.utc).isoformat()}",
        "",
        "phase timing (wall-clock, single CPU):",
    ]
    for line in header:
        print(line, flush=True)
        trace.append(line)

    t_start = time.perf_counter()
    t = t_start

    # phase 1 — load prior
    prior = json.loads(PRIOR_PATH.read_text())
    t = _phase(t, f"loaded prior              ({PRIOR_PATH.name}, {len(prior['raw_scores'])} dims)", trace)

    # phase 2 — load rubric
    rubric = Rubric.load(RUBRIC_PATH)
    t = _phase(t, f"loaded rubric             ({len(rubric.active_dim_ids)} active dims)", trace)

    # phase 3 — build eval_outputs
    evidence_uri = "https://priors.beaai.local/pi-ai-2026-05-06"
    eval_outputs = {
        d: {"raw": float(v), "evidence_uri": evidence_uri}
        for d, v in prior["raw_scores"].items()
    }
    payload = json.dumps(eval_outputs, sort_keys=True).encode()
    snapshot_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
    forensic_bundle_uri = "https://bundles.beaai.local/pi-ai/2026-05-06"
    (OUT_DIR / "scan_pi_ai_eval_outputs.json").write_text(json.dumps(eval_outputs, indent=2))
    t = _phase(t, "built eval_outputs        (priors → 21 dim raws, snapshot_hash sealed)", trace)

    # phase 4 — score
    score_resp = score(
        rubric, eval_outputs,
        snapshot_hash=snapshot_hash,
        forensic_bundle_uri=forensic_bundle_uri,
        scored_at=datetime.now(timezone.utc),
    )
    (OUT_DIR / "scan_pi_ai_score.json").write_text(json.dumps(score_resp, indent=2, default=str))
    t = _phase(t, f"scored                    (ARE={score_resp['bmi']}, band={score_resp['band']['display']})", trace)

    # phase 5 — quote (or referral)
    rates = Rates.load(RATES_PATH)
    customer = {
        "sector": "ENT_SAAS",
        "annual_revenue_usd": 100_000_000,
        "limit": {"per_claim_usd": 1_000_000, "aggregate_usd": 2_000_000},
    }
    try:
        q = quote(score_resp, customer, rates=rates)
        annual = q["annual_premium_usd"]
        total = q["total_with_endorsements_usd"]
        if total != annual:
            premium_str = f"${annual:,} (+${total - annual:,} endorsements = ${total:,})"
        else:
            premium_str = f"${annual:,} annual"
        (OUT_DIR / "scan_pi_ai_quote.json").write_text(json.dumps(q, indent=2, default=str))
        quote_status = f"indicative quote: {premium_str}"
    except ManualReferral as exc:
        (OUT_DIR / "scan_pi_ai_quote.json").write_text(json.dumps({"manual_referral_reason": exc.reason}, indent=2))
        quote_status = f"manual referral — {exc.reason[:80]}"
    t = _phase(t, f"priced                    ({quote_status})", trace)

    # phase 6 — bind eligibility
    verdict = evaluate_bind_eligibility(score_resp)
    eligible = "BIND-ELIGIBLE" if verdict["eligible"] else "NO-BIND"
    t = _phase(t, f"bind-eligibility check    ({eligible}; {len(verdict['reasons'])} reason(s))", trace)

    # phase 7 — report card render
    card_html = render(
        score_resp, rubric,
        provenance="prior — directional priors only (pi.ai blocks scraping; no live probe)",
        dim_provenance={dim: "prior" for dim in eval_outputs},
    )
    card_path = OUT_DIR / "scan_pi_ai_card.html"
    card_path.write_text(card_html)
    t = _phase(t, f"rendered report card      ({card_path.name}, {len(card_html):,} bytes)", trace)

    total_ms = (t - t_start) * 1000
    summary = [
        "",
        "=" * 60,
        "RESULTS",
        "=" * 60,
        f"  Target:           Pi (Inflection AI)",
        f"  ARE:              {score_resp['bmi']} / 100",
        f"  Band:             {score_resp['band']['display']}",
        f"  Posture:          {score_resp['band']['posture']}",
        f"  Pillar scores:    P1={score_resp['pillars']['P1']['score']:.2f}/35   "
        f"P2={score_resp['pillars']['P2']['score']:.2f}/20   "
        f"P3={score_resp['pillars']['P3']['score']:.2f}/30   "
        f"P4={score_resp['pillars']['P4']['score']:.2f}/15",
        f"  Quote:            {quote_status}",
        f"  Bind-eligibility: {eligible}",
    ]
    if verdict["reasons"]:
        for r in verdict["reasons"]:
            summary.append(f"     reason: {r}")
    summary += [
        "",
        f"  Total wall-clock: {total_ms:.1f} ms (priors → ARE → quote → report card)",
        "",
        "Artifacts:",
        f"  {(OUT_DIR / 'scan_pi_ai_eval_outputs.json').relative_to(REPO)}",
        f"  {(OUT_DIR / 'scan_pi_ai_score.json').relative_to(REPO)}",
        f"  {(OUT_DIR / 'scan_pi_ai_quote.json').relative_to(REPO)}",
        f"  {(OUT_DIR / 'scan_pi_ai_card.html').relative_to(REPO)}",
    ]
    for line in summary:
        print(line, flush=True)
        trace.append(line)

    (OUT_DIR / "scan_pi_ai_trace.txt").write_text("\n".join(trace) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
