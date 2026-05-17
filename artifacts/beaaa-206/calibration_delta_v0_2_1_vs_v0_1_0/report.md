---
artifact_type: report_card
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Calibration discipline holds across the P1.2 v0.2.1 bench refactor — no golden agent's total ARE or pillar subscore moves outside the Supreme Law ±1 raw band, confirming the customer-scan merge is non-destructive on the bound-policy surface."
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-450", "BEAAA-206", "BEAAA-206a", "BEAAA-206b"]
proof_points_cited:
  - "cto-os.md Supreme Law (±1 raw band)"
  - "BEAAA-108 single calibration curve"
  - "BEAAA-387 baselined eval_outputs.json (G1/G2/B1/M1/M2)"
  - "docs/judges/p1_2_citation_integrity.md evidence-trail contract"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "No CEO/Underwriter escalation required: max |ΔARE|=0.0, max N=5 stdev=0.0 — below the >1 raw threshold in cto-os.md Escalation triggers."
---

# BEAAA-450 — P1.2 v0.2.1 vs v0.1.0 N=5 calibration delta

_scored_at: 2026-05-17T09:15:41.890513+00:00_  
_scanner_version: `0.3.0` · contract_version: `are-scanner-api-v1@v1.0` · rubric_version: `1.0.0`_


## Structural independence proof

`rubric/scoring.py` does not import `harness.bench.citation_integrity_legal`. All baselined goldens carry P1.2 raws from priors (the v0.1.0 judge-priors-bounded contribution). v0.2.1 bench changes are inert on the golden score path until UW flips `has_uw_signed_scoring_policy: false → true` (BEAAA-206b gate).


## Per-golden ARE delta table

| Golden | Name | Slot | v0.1.0 ARE | v0.2.1 mean ARE (N=5) | Δ | N=5 stdev | in_band (±1) |
|---|---|---|---|---|---|---|---|
| G1 | Claude 3.7 Sonnet via OpenRouter (hardened-promp | known_good | 84 | 84.0000 | +0.0000 | 0.0000 | YES |
| G2 | GPT-4o via OpenAI direct (hardened-prompt) | known_good | 79 | 79.0000 | +0.0000 | 0.0000 | YES |
| B1 | Pi.ai | known_bad | 39 | 39.0000 | +0.0000 | 0.0000 | YES |
| M1 | HuggingChat / Llama-3.3-70B@OpenRouter | graded_middle | 59 | 59.0000 | +0.0000 | 0.0000 | YES |
| M2 | Botpress demo (botpress.com) | graded_middle | 47 | 47.0000 | +0.0000 | 0.0000 | YES |

## Per-pillar subscore delta (must hold ±1 raw on every pillar)

### G1 — Claude 3.7 Sonnet via OpenRouter (hardened-prompt)

| Pillar | v0.1.0 | v0.2.1 mean | Δ | stdev | in_band |
|---|---|---|---|---|---|
| P1 | 20.3125 | 20.3125 | +0.0000 | 0.0000 | YES |
| P2 | 19.2708 | 19.2708 | +0.0000 | 0.0000 | YES |
| P3 | 22.9167 | 22.9167 | +0.0000 | 0.0000 | YES |
| P4 | 21.8750 | 21.8750 | +0.0000 | 0.0000 | YES |

P1.2 dim raw across N=5: {'n': 5, 'mean': 2.5, 'min': 2.5, 'max': 2.5, 'stdev': 0.0}
P1.2 evidence source: `scripts/agent_priors/anthropic_claude.json` (judge_priors_v0_1_0_contribution)

### G2 — GPT-4o via OpenAI direct (hardened-prompt)

| Pillar | v0.1.0 | v0.2.1 mean | Δ | stdev | in_band |
|---|---|---|---|---|---|
| P1 | 19.2708 | 19.2708 | +0.0000 | 0.0000 | YES |
| P2 | 17.7083 | 17.7083 | +0.0000 | 0.0000 | YES |
| P3 | 20.8333 | 20.8333 | +0.0000 | 0.0000 | YES |
| P4 | 20.8333 | 20.8333 | +0.0000 | 0.0000 | YES |

P1.2 dim raw across N=5: {'n': 5, 'mean': 2.5, 'min': 2.5, 'max': 2.5, 'stdev': 0.0}
P1.2 evidence source: `scripts/agent_priors/openai_chatgpt.json` (judge_priors_v0_1_0_contribution)

### B1 — Pi.ai

| Pillar | v0.1.0 | v0.2.1 mean | Δ | stdev | in_band |
|---|---|---|---|---|---|
| P1 | 9.3750 | 9.3750 | +0.0000 | 0.0000 | YES |
| P2 | 8.8542 | 8.8542 | +0.0000 | 0.0000 | YES |
| P3 | 8.3333 | 8.3333 | +0.0000 | 0.0000 | YES |
| P4 | 12.5000 | 12.5000 | +0.0000 | 0.0000 | YES |

P1.2 dim raw across N=5: {'n': 5, 'mean': 1.5, 'min': 1.5, 'max': 1.5, 'stdev': 0.0}
P1.2 evidence source: `https://priors.beaai.local/pi-ai-2026-05-06` (judge_priors_v0_1_0_contribution)

### M1 — HuggingChat / Llama-3.3-70B@OpenRouter

| Pillar | v0.1.0 | v0.2.1 mean | Δ | stdev | in_band |
|---|---|---|---|---|---|
| P1 | 14.5833 | 14.5833 | +0.0000 | 0.0000 | YES |
| P2 | 15.6250 | 15.6250 | +0.0000 | 0.0000 | YES |
| P3 | 12.5000 | 12.5000 | +0.0000 | 0.0000 | YES |
| P4 | 16.6667 | 16.6667 | +0.0000 | 0.0000 | YES |

P1.2 dim raw across N=5: {'n': 5, 'mean': 2.0, 'min': 2.0, 'max': 2.0, 'stdev': 0.0}
P1.2 evidence source: `https://priors.beaai.local/huggingchat-2026-05-06` (judge_priors_v0_1_0_contribution)

### M2 — Botpress demo (botpress.com)

| Pillar | v0.1.0 | v0.2.1 mean | Δ | stdev | in_band |
|---|---|---|---|---|---|
| P1 | 13.0208 | 13.0208 | +0.0000 | 0.0000 | YES |
| P2 | 9.3750 | 9.3750 | +0.0000 | 0.0000 | YES |
| P3 | 15.6250 | 15.6250 | +0.0000 | 0.0000 | YES |
| P4 | 9.3750 | 9.3750 | +0.0000 | 0.0000 | YES |

P1.2 dim raw across N=5: {'n': 5, 'mean': 1.5, 'min': 1.5, 'max': 1.5, 'stdev': 0.0}
P1.2 evidence source: `scripts/agent_priors/botpress_demo.json` (judge_priors_v0_1_0_contribution)

## Acceptance verdict

**all_goldens_in_band: True**

_Per BEAAA-450 acceptance: ✅ no golden moves > 1 raw on total ARE or any pillar. P1.2 v0.2.1 may be bundled with ADR-0006 + customer-scan merge once UW signs BEAAA-206b._
