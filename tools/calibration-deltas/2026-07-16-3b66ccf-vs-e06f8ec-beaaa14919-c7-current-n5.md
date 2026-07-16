---
artifact_type: calibration_delta
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-14919 proves the refreshed C7 protocol/review reconciliation remains inside the BackBond Scanner ±1 golden calibration envelope after current-master refresh."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1"]
brand_backbone_applicable: false
brand_backbone_verified: false
brand_backbone_version: null
brand_backbone_sha256: null
author_role: scanner_engineer
artifact_version: "v0.1"
---

# BEAAA-14919 C7 Current-Base N=5 Golden Calibration Delta

BLUF: PASS. Current base `3b66ccfdec700e6d8037e64757f8127d14c2bcbb` and refreshed candidate `e06f8ecfd7fe97b38348ef627c7841f61f8d8dcb` produce identical live-golden score outputs for seeds 0-4. Every total and pillar delta is `0`, and every baseline/candidate spread is `0`, so the refreshed branch remains within the required ±1 envelope.

- ticket: `BEAAA-14919`
- PR: `#114`
- contract anchor requested for merge review: `are-scanner-api-v1@v1.0`
- scanner_version: `0.5.0`
- package-reported contract_version: `are-scanner-api-v1@v1.3.0`
- corpus_version: `fixture-pinned per live golden expected_score.json`
- probe_set_version: `golden_agents_v1`
- rubric_point_id: `release-wide / existing active BackBond Scanner rubric points`
- N=5 calibration delta: `PASS; max_abs_total_delta=0; max_abs_pillar_delta=0; max_spread=0`
- N=5 seeds: `0, 1, 2, 3, 4`
- result: `PASS`
- cto_cosign_ref: `PR #114 CTO merge review pending`

## Method

`origin/master` advanced to `3b66ccf`, so the C7 branch was refreshed onto that base and the score-direct golden N=5 path was rerun. The path rebuilds policy-derived applicability decisions from fixture `architecture_flags`, validates them through `validate_for_scoring`, then calls `rubric.scoring.score()` for seeds 0-4 on each ref using the same test-only signing key on both sides.

## Delta Table

| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | baseline spread | candidate spread |
|---|---:|---:|---:|---:|---:|---|---|
| B1 | +0 | +0 | +0 | +0 | +0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| G1 | +0 | +0 | +0 | +0 | +0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| G2 | +0 | +0 | +0 | +0 | +0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| M1 | +0 | +0 | +0 | +0 | +0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| M2 | +0 | +0 | +0 | +0 | +0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| M3 | +0 | +0 | +0 | +0 | +0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |

## Failures

- none

## Raw Outputs

- baseline raw run: `/tmp/beaaa14919-n5-score-direct/baseline-3b66ccf.json`
- candidate raw run: `/tmp/beaaa14919-n5-score-direct/candidate-e06f8ec.json`
- committed comparison JSON: `tools/calibration-deltas/2026-07-16-3b66ccf-vs-e06f8ec-beaaa14919-c7-current-n5.json`
