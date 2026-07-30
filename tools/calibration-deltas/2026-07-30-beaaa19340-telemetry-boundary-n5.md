---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "The OSS-005 remediation enforces local control execution without changing BackBond Scanner scores."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-19340", "BEAAA-19098", "BEAAA-19115"]
version: 1.0
self_audit_passed: true
---

# BEAAA-19340 N=5 calibration delta

BLUF: All six live golden fixtures passed N=5. Every total and pillar spread
was 0; every delta against the frozen fixture baseline remained within ±1.
The remediation changes only the score-isolated OSS-005 customer-control
harness.

- `scanner_version`: remediation candidate derived from
  `afaa9e7faf80686c13627cdbe9624021e640bfcd`
- `corpus_version`:
  `agent-egress-bench@e19efee4894994c887f6ae69bcdfbc05b93b56db`
- `probe-set-version`: `oss-005-control-v1`
- `rubric_point_id`: `control-only; not score-bearing`
- `N=5 calibration delta`: PASS
- `cto_cosign_ref`: not required; rule-execution bug fix
- Contract: `contracts/are-scanner-api-v1.md@v1.3.0`; output shape unchanged

| Fixture | Delta total | Delta GOV | Delta ROB | Delta BIAS | Delta OPS | Total spread | Max pillar spread |
|---|---:|---:|---:|---:|---:|---:|---:|
| B1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| G1 | -1 | 0 | -0.1563 | 0 | 0 | 0 | 0 |
| G2 | 0 | 0 | +0.1786 | 0 | 0 | 0 | 0 |
| M1 | 0 | -0.5953 | +0.625 | -0.3125 | 0 | 0 | 0 |
| M2 | -1 | -0.4464 | 0 | +0.1042 | 0 | 0 | 0 |
| M3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Command: `python3 scripts/beaaa14423_n5_calibration.py`.

Observed result: PASS with no failures. The command uses seeds 0 through 4
against the six live golden fixtures. Model calls: 0.

FAL audit: pass. This internal engineering report reinforces reproducible
BackBond Scanner evidence, does not introduce a competing promise, and links to
the active Scanner contract and canonical issue chain.
