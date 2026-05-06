"""BEAAA-130 follow-up — BMI Scanner v0.1 against HuggingChat (huggingface.co/chat).

Picked because it is a *publicly-open* AI agent the board can actually load
in a browser and interact with — no login required for anonymous chat. That
matches the BEAAA-130 follow-up ask: "find me something I can access and
see the agent in action."

Single-pass priors-based scan with phase-by-phase wall-clock timing. Same
pipeline shape as scan_pi_ai.py — swapped prior path, swapped target
metadata, evidence URIs point to HF's public surfaces.

Outputs:
  - artifacts/beaaa-130/scan_huggingchat_trace.txt
  - artifacts/beaaa-130/scan_huggingchat_eval_outputs.json
  - artifacts/beaaa-130/scan_huggingchat_score.json
  - artifacts/beaaa-130/scan_huggingchat_quote.json
  - artifacts/beaaa-130/scan_huggingchat_card.html
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

PRIOR_PATH = REPO / "scripts" / "agent_priors" / "huggingchat.json"
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
RATES_PATH = REPO / "pricing" / "rates.yaml"
OUT_DIR = REPO / "artifacts" / "beaaa-130"

TARGET_NAME = "HuggingChat (Hugging Face)"
TARGET_URL = "https://huggingface.co/chat"
SCAN_MODE = "directional priors (board can verify by chatting at huggingface.co/chat)"


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
        "BMI Scanner v0.1 — BEAAA-130 follow-up run",
        f"  target:        {TARGET_NAME}",
        f"  public url:    {TARGET_URL}  (board can interact directly — no login)",
        f"  scan mode:     {SCAN_MODE}",
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

    prior = json.loads(PRIOR_PATH.read_text())
    t = _phase(t, f"loaded prior              ({PRIOR_PATH.name}, {len(prior['raw_scores'])} dims)", trace)

    rubric = Rubric.load(RUBRIC_PATH)
    t = _phase(t, f"loaded rubric             ({len(rubric.active_dim_ids)} active dims)", trace)

    evidence_uri = "https://priors.beaai.local/huggingchat-2026-05-06"
    eval_outputs = {
        d: {"raw": float(v), "evidence_uri": evidence_uri}
        for d, v in prior["raw_scores"].items()
    }
    payload = json.dumps(eval_outputs, sort_keys=True).encode()
    snapshot_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
    forensic_bundle_uri = "https://bundles.beaai.local/huggingchat/2026-05-06"
    (OUT_DIR / "scan_huggingchat_eval_outputs.json").write_text(json.dumps(eval_outputs, indent=2))
    t = _phase(t, "built eval_outputs        (priors → 21 dim raws, snapshot_hash sealed)", trace)

    score_resp = score(
        rubric, eval_outputs,
        snapshot_hash=snapshot_hash,
        forensic_bundle_uri=forensic_bundle_uri,
        scored_at=datetime.now(timezone.utc),
    )
    (OUT_DIR / "scan_huggingchat_score.json").write_text(json.dumps(score_resp, indent=2, default=str))
    t = _phase(t, f"scored                    (BMI={score_resp['bmi']}, band={score_resp['band']['display']})", trace)

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
        (OUT_DIR / "scan_huggingchat_quote.json").write_text(json.dumps(q, indent=2, default=str))
        quote_status = f"indicative quote: {premium_str}"
    except ManualReferral as exc:
        (OUT_DIR / "scan_huggingchat_quote.json").write_text(json.dumps({"manual_referral_reason": exc.reason}, indent=2))
        quote_status = f"manual referral — {exc.reason[:80]}"
    t = _phase(t, f"priced                    ({quote_status})", trace)

    verdict = evaluate_bind_eligibility(score_resp)
    eligible = "BIND-ELIGIBLE" if verdict["eligible"] else "NO-BIND"
    t = _phase(t, f"bind-eligibility check    ({eligible}; {len(verdict['reasons'])} reason(s))", trace)

    card_html = render(
        score_resp, rubric,
        provenance="prior — directional priors only (no measurements, no live probe)",
        dim_provenance={dim: "prior" for dim in eval_outputs},
    )
    card_path = OUT_DIR / "scan_huggingchat_card.html"
    card_path.write_text(card_html)
    t = _phase(t, f"rendered report card      ({card_path.name}, {len(card_html):,} bytes)", trace)

    total_ms = (t - t_start) * 1000
    summary = [
        "",
        "=" * 60,
        "RESULTS",
        "=" * 60,
        f"  Target:           {TARGET_NAME}",
        f"  Public URL:       {TARGET_URL}",
        f"  BMI:              {score_resp['bmi']} / 100",
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
        f"  Total wall-clock: {total_ms:.1f} ms (priors → BMI → quote → report card)",
        "",
        "Artifacts:",
        f"  {(OUT_DIR / 'scan_huggingchat_eval_outputs.json').relative_to(REPO)}",
        f"  {(OUT_DIR / 'scan_huggingchat_score.json').relative_to(REPO)}",
        f"  {(OUT_DIR / 'scan_huggingchat_quote.json').relative_to(REPO)}",
        f"  {(OUT_DIR / 'scan_huggingchat_card.html').relative_to(REPO)}",
    ]
    for line in summary:
        print(line, flush=True)
        trace.append(line)

    (OUT_DIR / "scan_huggingchat_trace.txt").write_text("\n".join(trace) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
