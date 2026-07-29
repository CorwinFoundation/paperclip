---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "The score-isolated OSS-005 control harness preserves the BackBond Scanner N=5 reproducibility band."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-19098", "BEAAA-19106"]
version: 1.0
self_audit_passed: true
---

# BEAAA-19098 N=5 calibration delta

BLUF: All six live golden fixtures passed N=5. Every total and pillar spread
was 0; all deltas against the frozen fixture baseline remained within ±1.
OSS-005 is control-only and imports no scoring or agent-behavior registry path.

- `scanner_version`: repository-pinned candidate
- `corpus_version`: `agent-egress-bench@e19efee4894994c887f6ae69bcdfbc05b93b56db`
- `probe-set-version`: `oss-005-control-v1`
- `rubric_point_id`: `control-only; not score-bearing`
- `N=5 calibration delta`: PASS
- `cto_cosign_ref`: `[BEAAA-19106](/BEAAA/issues/BEAAA-19106)`
- Contract: `contracts/are-scanner-api-v1.md@v1.0`

| Fixture | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | Total spread | Max pillar spread |
|---|---:|---:|---:|---:|---:|---:|---:|
| B1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| G1 | -1 | 0 | -0.1563 | 0 | 0 | 0 | 0 |
| G2 | 0 | 0 | +0.1786 | 0 | 0 | 0 | 0 |
| M1 | 0 | -0.5953 | +0.625 | -0.3125 | 0 | 0 | 0 |
| M2 | -1 | -0.4464 | 0 | +0.1042 | 0 | 0 | 0 |
| M3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Command: in-memory replay using
`scripts/beaaa14423_n5_calibration.py` scoring helpers, seeds 0–4, with no
artifact overwrite. Model calls: 0.
