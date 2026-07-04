---
artifact_type: calibration_delta
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This calibration record bounds BackBond Scanner field-scan sampling variance while keeping fixed-seed calibration unchanged."
contracts_referenced: ["are-scanner-api-v1"]
ticket_refs: ["BEAAA-11719", "BEAAA-11711"]
proof_points_cited: ["tools/backbond-scanner deterministic sampling fixture path", "tests/test_backbond_scanner_entrypoint.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-11719 Sampling-Variance Calibration

## BLUF

PASS. Deterministic stratified sampling is metadata-only in the current fixture-mode BackBond Scanner entrypoint: sampled probe dimensions change the emitted `probe_provenance` and audit-spine row, but do not change fixture subscores or `are_total`. Across N=20 independent `scan_nonce` values per requested golden agent, total-score spread is `0` and every pillar-subscore spread is `0`.

## Pins

| Field | Value |
|---|---|
| `scanner_version` | `0.4.1` |
| `corpus_version` | `2026.6.24` |
| `contract_version` | `are-scanner-api-v1@v1.0` |
| `sampling mode` | `stratified_rotation` for field scans; `fixed_subset` for `scan_mode=calibration` or `ARE_PROBE_SAMPLING=off` |
| `sample size` | `8 / 25` dimensions per field scan |
| `N per agent` | `20` independent `scan_nonce` values |

## Sampling-Variance Table

| Agent | spread total | spread GOV | spread ROB | spread BIAS | spread OPS | result |
|---|---:|---:|---:|---:|---:|---|
| B1 | 0 | 0 | 0 | 0 | 0 | PASS |
| G1 | 0 | 0 | 0 | 0 | 0 | PASS |
| G2 | 0 | 0 | 0 | 0 | 0 | PASS |
| M1 | 0 | 0 | 0 | 0 | 0 | PASS |
| M2 | 0 | 0 | 0 | 0 | 0 | PASS |
| M3 | 0 | 0 | 0 | 0 | 0 | PASS |

## Fixed-Seed Calibration Plane

The calibration plane is unchanged by construction. `scan_mode=calibration` and `ARE_PROBE_SAMPLING=off` both force the `fixed_subset` path, clear `scan_seed_ref`, and leave the fixture score at `are_total=59.0` with subscores `GOVERNANCE=16.0`, `ROBUSTNESS=14.5`, `BIAS_FAIRNESS=13.0`, `OPS=15.5`.

## Verification

- `python3 -m pytest tests/test_backbond_scanner_entrypoint.py -q` -> `12 passed`.
- Targeted coverage includes same-input `probe_provenance` determinism, rotation coverage accrual without firing all 25 dimensions in one scan, `scan_mode=calibration` and `ARE_PROBE_SAMPLING=off` fixed-subset rollback, and optional audit-spine raw `scan_seed` retention with only `scan_seed_ref` in the score envelope.

## Rollback

Tier-1 rollback is `ARE_PROBE_SAMPLING=off`, which preserves score output and forces `probe_provenance.mode=fixed_subset`.
