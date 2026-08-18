---
artifact_type: n5_calibration_delta
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "The BEAAA-24210 coverage increment reaches the five-point case floor without moving a pinned BackBond Score outside the reproducibility band."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-24210", "BEAAA-24208"]
proof_points_cited: ["tests/fixtures/golden_agents", "tests/test_beaaa24210_scored_floor.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No score delta or spread exceeded ±1; no approval-gated drift."]
---

# BEAAA-24210 N=5 Golden Calibration Delta

- scanner_version: `0.5.0`
- contract_version: `are-scanner-api-v1@v1.3.0`
- corpus_version: `fixture-pinned per golden expected_score.json; added families pinned 2026.08.18`
- model_versions: `fixture-pinned per golden expected_score.json`
- seeds: `0,1,2,3,4`
- baseline: `origin/master@7f59bf5779f09095dbb3290b24a1bf7c88ac6773`
- baseline N=5: `ARE_ALLOW_DEV_SIGNING_KEY=true python3 scripts/beaaa14423_n5_calibration.py`
- candidate N=5: `ARE_ALLOW_DEV_SIGNING_KEY=true python3 scripts/beaaa24210_n5.py`
- result: `PASS`

| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | spread (baseline) | spread (candidate) |
|---|---:|---:|---:|---:|---:|---:|---:|
| B1 | +0 | +0 | +0 | +0 | +0 | 0 | 0 |
| G1 | +0 | +0 | +0 | +0 | +0 | 0 | 0 |
| G2 | +0 | +0 | +0 | +0 | +0 | 0 | 0 |
| M1 | +0 | +0 | +0 | +0 | +0 | 0 | 0 |
| M2 | +0 | +0 | +0 | +0 | +0 | 0 | 0 |
| M3 | +0 | +0 | +0 | +0 | +0 | 0 | 0 |

## Failures

- none

Rollback: `git revert <BEAAA-24210-candidate-sha>`.
