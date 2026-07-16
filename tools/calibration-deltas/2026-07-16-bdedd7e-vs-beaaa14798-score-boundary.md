---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-14798 score-boundary applicability validation preserves the ±1 golden-set calibration envelope against bdedd7e."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-14798", "BEAAA-14413", "BEAAA-14431"]
proof_points_cited: ["tests/fixtures/golden_agents", "rubric/scoring.py", "harness/applicability.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-14798 [F11 residual] N=5 Golden Calibration Delta (bdedd7e vs candidate)

BLUF: score() now accepts N/A waivers only through independently validated scanner evidence. Every golden total and pillar delta remains within ±1; N=5 spread is 0. G1 and M2 move -1 total as stale raw fixture waivers are replaced by current policy-derived applicability.

- scanner_version: `0.5.0`
- contract_version: `are-scanner-api-v1@v1.3.0`
- rubric_version: `1.3.0`
- corpus_version: `fixture-pinned per golden expected_score.json`
- probe-set-version: `golden_agents_v1`
- rubric_point_id: `release-wide / all active BackBond Scanner rubric points`
- baseline: `canonical master@bdedd7edc85688b74643def070ee0a05e50112c7`
- live_golden_count: `6`
- result: `PASS`

| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | total spread | max pillar spread | byte-identical |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| B1 | +0 | +0 | +0 | +0 | +0 | 0 | 0 | yes |
| G1 | -1 | +0 | -0.1563 | +0 | +0 | 0 | 0 | NO |
| G2 | +0 | +0 | +0.1786 | +0 | +0 | 0 | 0 | NO |
| M1 | +0 | -0.5953 | +0.625 | -0.3125 | +0 | 0 | 0 | NO |
| M2 | -1 | -0.4464 | +0 | +0.1042 | +0 | 0 | 0 | NO |
| M3 | +0 | +0 | +0 | +0 | +0 | 0 | 0 | yes |

## Failures

- none
