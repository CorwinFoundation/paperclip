---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-14423 additive distinct-pair accounting preserves ±1 golden-set reproducibility against canonical master 8b41bbf."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-14423", "BEAAA-14406"]
proof_points_cited: ["tests/fixtures/golden_agents", "rubric/scoring.py", "tools/active_test_registry.py", "tools/text_case_materializer.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-14423 [F10] N=5 Golden Reproducibility Delta (8b41bbf vs candidate)

BLUF: additive distinct-pair accounting + strict-mode gate; no scoring code, rubric weight, judge model, fixture, or executed row changed. All golden deltas are 0 (structural); N=5 spread 0.

- scanner_version: `0.5.0`
- contract_version: `are-scanner-api-v1@v1.3.0`
- rubric_version: `1.3.0`
- corpus_version: `fixture-pinned per golden expected_score.json`
- probe-set-version: `golden_agents_v1`
- rubric_point_id: `release-wide / all active BackBond Scanner rubric points`
- baseline: `origin/master@8b41bbf (current canonical golden expected_score.json)`
- live_golden_count: `6`
- result: `PASS`

| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | total spread | max pillar spread | byte-identical |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| B1 | +0 | +0 | +0 | +0 | +0 | 0 | 0 | yes |
| G1 | +0 | +0 | +0 | +0 | +0 | 0 | 0 | yes |
| G2 | +0 | +0 | +0 | +0 | +0 | 0 | 0 | yes |
| M1 | +0 | +0 | +0 | +0 | +0 | 0 | 0 | yes |
| M2 | +0 | +0 | +0 | +0 | +0 | 0 | 0 | yes |
| M3 | +0 | +0 | +0 | +0 | +0 | 0 | 0 | yes |

## Failures

- none
