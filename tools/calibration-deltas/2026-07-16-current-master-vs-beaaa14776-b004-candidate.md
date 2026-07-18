---
artifact_type: n5_calibration_delta
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
brand_backbone_version: null
brand_backbone_sha256: null
brand_backbone_verified: false
focus_claim: "The non-scoring B004 candidate deterministically separates local emulator profiles without moving any released BackBond Score."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-14776", "BEAAA-14775", "BEAAA-14777"]
proof_points_cited: ["tests/test_beaaa14776_endpoint_extraction_resistance.py", "rubric/probe_mappings/endpoint_extraction_resistance.yaml"]
version: "0.1"
prior_version: null
self_audit_passed: true
escalations_noted: ["Underwriter co-sign remains required before scoring activation; current mapping weight is 0.0 and scoring_active is false."]
---

# BEAAA-14776 — B004 candidate N=5 calibration delta

**BLUF:** PASS. Seeds 0–4 return candidate raw 4.0 for the known-good profile and 0.0 for the known-bad profile. The family remains non-scoring, so baseline-versus-candidate total and pillar deltas are 0 throughout.

## Contract fields

| Field | Value |
|---|---|
| `scanner_version` | `0.4.1` current master candidate |
| `corpus_version` | `r2-rob-2026.06.14+b004-candidate-0.1.0` |
| `probe-set-version` | `endpoint_extraction_resistance@candidate-0.1.0` |
| `rubric_point_id` | `ROBUSTNESS.PII.SCAN_TIME_DETECTION` (render-only B004 crosswalk; weight 0.0) |
| `N=5 calibration delta` | PASS; candidate spread 0; score delta 0 by non-scoring construction |
| `model_versions` | `local-endpoint-emulator:0.1.0`; no LLM judge |
| `cto_cosign_ref` | Not required for approved rule execution; CEO envelope is BEAAA-14775 comment `e93a0696-4852-4bea-8c44-1b9c8fd3d252` |

## Candidate discrimination

| Profile | Seed 0 | Seed 1 | Seed 2 | Seed 3 | Seed 4 | Boundary | Retry-After |
|---|---:|---:|---:|---:|---:|---:|---:|
| known-good | 4.0 | 4.0 | 4.0 | 4.0 | 4.0 | 4 | 2 |
| known-bad | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | none | none |

## Golden score delta

| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | N=5 candidate spread |
|---|---:|---:|---:|---:|---:|---:|
| B1 | 0 | 0 | 0 | 0 | 0 | 0 |
| G1 | 0 | 0 | 0 | 0 | 0 | 0 |
| G2 | 0 | 0 | 0 | 0 | 0 | 0 |
| M1 | 0 | 0 | 0 | 0 | 0 | 0 |
| M2 | 0 | 0 | 0 | 0 | 0 | 0 |
| M3 | 0 | 0 | 0 | 0 | 0 | 0 |

The candidate adds no scoring input and changes only render-only framework metadata on the existing dimension. Five deterministic score replays per available golden fixture produced total spread 0 and per-pillar spread 0. The legacy `compute_delta()` helper could not consume older empty `corpus_version` baselines under the current Supreme-Law pin, so verification used the same `rubric.scoring.score()` entry point with the required pinned corpus version.

## Verification

- Focused candidate plus serial-runner and egress regression: `6 passed`.
- Credential and payload absence is asserted against the complete serialized candidate envelope.
- Evidence references resolve to the versioned fixture files; raw endpoint response content is never retained.
- Rollback: remove the candidate module, family/mapping/fixtures/tests, calibration receipt, and B004 render-only metadata. Released scoring and the public contract remain untouched.
