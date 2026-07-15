---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-14087 preserves BackBond Scanner scoring output while hardening authenticated executed-agent evidence counting."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-14087", "BEAAA-14634"]
proof_points_cited: ["scripts/beaaa6190_v05_rc_n5.py", "tests/fixtures/golden_agents", "rubric/scoring.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-14087 Baseline vs Candidate N=5 Delta

BLUF: The candidate produces zero total and per-pillar movement against canonical master for every live golden agent. All five repetitions per agent were byte-identical to the frozen expected output in both worktrees.

- baseline: `origin/master@dd3799242b3b0326f9af34fbe90e9d2b602080cf`
- candidate measured: `f5c502ae090c9ef5e8264e40a4e1d026a2e5b00d`
- scanner_version: `0.5.0`
- contract_version: `are-scanner-api-v1@v1.3.0`
- corpus_version: `fixture-pinned per golden expected_score.json`
- probe-set-version: `golden_agents_v1`
- rubric_point_id: `release-wide/all active BackBond Scanner rubric points`
- repetitions: `N=5 per live agent per worktree`
- result: `PASS`

| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | spread (baseline) | spread (candidate) |
|---|---:|---:|---:|---:|---:|---:|---:|
| B1 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 |
| G1 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 |
| G2 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 |
| M1 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 |
| M2 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 |
| M3 | 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 |

`B2`, `B3`, and `G3` are pending baseline scans in both worktrees and are excluded symmetrically; they contribute no measured score or pillar delta.

Method: run `python3 scripts/beaaa6190_v05_rc_n5.py` independently in an isolated `origin/master@dd37992` worktree and in the candidate worktree. Compare each live agent's first total and pillar scores and confirm all five within-worktree repetitions remain byte-identical to `expected_score.json`.
