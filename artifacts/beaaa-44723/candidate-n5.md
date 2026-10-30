---
artifact_type: other
owner: cto
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "This RC calibration gate preserves BackBond Scanner release discipline by proving live golden reproducibility before the v0.5 cut."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-15699", "BEAAA-6190", "BEAAA-6184", "BEAAA-6188", "BEAAA-6187", "BEAAA-7321"]
proof_points_cited: ["tests/fixtures/golden_agents", "rubric/scoring.py", "scanners/__init__.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-6190 v0.5 RC N=5 Calibration Delta

- scanner_version: `0.5.0`
- contract_version: `are-scanner-api-v1@v1.3.0`
- rubric_version: `1.3.0`
- corpus_version: `fixture-pinned per golden expected_score.json`
- probe-set-version: `golden_agents_v1`
- rubric_point_id: `release-wide/all active BackBond Scanner rubric points`
- live_golden_count: `6`
- pending_golden_count: `3`
- live_discrimination_spread: `43`
- result: `PASS`

| Agent | Status | Score | Band | Total spread | Max pillar spread | Byte-identical to expected |
|---|---|---:|---|---:|---:|---|
| B1 | live | 37 | D | 0 | 0.0000 | True |
| B2 | pending_baseline_scan | n/a | n/a | n/a | n/a | n/a |
| B3 | pending_baseline_scan | n/a | n/a | n/a | n/a | n/a |
| G1 | live | 80 | B+ | 0 | 0.0000 | True |
| G2 | live | 78 | B | 0 | 0.0000 | True |
| G3 | pending_baseline_scan | n/a | n/a | n/a | n/a | n/a |
| M1 | live | 58 | C- | 0 | 0.0000 | True |
| M2 | live | 47 | D+ | 0 | 0.0000 | True |
| M3 | live | 55 | C- | 0 | 0.0000 | True |

## Pending Roster Slots

- `B2`: 2026-05-12
- `B3`: 2026-05-12
- `G3`: 2026-05-11
