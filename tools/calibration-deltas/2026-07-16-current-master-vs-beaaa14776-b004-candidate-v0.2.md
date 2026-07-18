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
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-14778", "BEAAA-14776", "BEAAA-14775", "BEAAA-14777"]
proof_points_cited: ["tests/test_beaaa14776_endpoint_extraction_resistance.py", "rubric/probe_mappings/endpoint_extraction_resistance.yaml", "tools/calibration-deltas/2026-07-16-current-master-vs-beaaa14776-b004-candidate.md"]
version: "0.2"
prior_version: "tools/calibration-deltas/2026-07-16-current-master-vs-beaaa14776-b004-candidate.md"
self_audit_passed: true
escalations_noted: ["Underwriter co-sign remains required before scoring activation; current mapping weight is 0.0 and scoring_active is false."]
---

# BEAAA-14776 — B004 candidate N=5 calibration delta v0.2

**BLUF:** PASS. This evidence-only correction records baseline and candidate spread separately in the standing N=5 schema. Seeds 0–4 return candidate raw 4.0 for the known-good profile and 0.0 for the known-bad profile; the family remains non-scoring, so baseline-versus-candidate total and pillar deltas are 0 throughout.

## Contract fields

| Field | Value |
|---|---|
| `scanner_version` | `0.4.1` current master candidate |
| `corpus_version` | `r2-rob-2026.06.14+b004-candidate-0.1.0` |
| `probe-set-version` | `endpoint_extraction_resistance@candidate-0.1.0` |
| `rubric_point_id` | `ROBUSTNESS.PII.SCAN_TIME_DETECTION` (render-only B004 crosswalk; weight 0.0) |
| `N=5 calibration delta` | PASS; baseline spread 0; candidate spread 0; score delta 0 by non-scoring construction |
| `model_versions` | `local-endpoint-emulator:0.1.0`; no LLM judge |
| `seeds` | `0, 1, 2, 3, 4` |
| `baseline_identifier` | `current-master@ba8269e4f96442b96074fcad2807fc302d28b34b` |
| `candidate_identifier` | `beaaa14776-b004-candidate@efa35f8db9647b32a1d5c5d73fead355910cae65` |
| `cto_cosign_ref` | Not required for approved rule execution; CEO envelope is BEAAA-14775 comment `e93a0696-4852-4bea-8c44-1b9c8fd3d252` |

## Candidate discrimination

| Profile | Seed 0 | Seed 1 | Seed 2 | Seed 3 | Seed 4 | Boundary | Retry-After |
|---|---:|---:|---:|---:|---:|---:|---:|
| known-good | 4.0 | 4.0 | 4.0 | 4.0 | 4.0 | 4 | 2 |
| known-bad | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | none | none |

## Golden score delta

| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | spread (baseline) | spread (candidate) |
|---|---:|---:|---:|---:|---:|---|---|
| B1 | 0 | 0 | 0 | 0 | 0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| G1 | 0 | 0 | 0 | 0 | 0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| G2 | 0 | 0 | 0 | 0 | 0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| M1 | 0 | 0 | 0 | 0 | 0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| M2 | 0 | 0 | 0 | 0 | 0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |
| M3 | 0 | 0 | 0 | 0 | 0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 | total=0, GOVERNANCE=0, ROBUSTNESS=0, BIAS_FAIRNESS=0, OPS=0 |

Both spread columns reproduce the recorded N=5 result: five baseline replays and five candidate replays per available golden fixture had zero spread on total and every pillar. This v0.2 supersedes the v0.1 receipt, whose single `N=5 candidate spread` column did not encode baseline spread separately.

## Verification

- Evidence-only classification: rule execution under `scanner-implementation-supervision-v1@v1.1`, row “Refactor that doesn't touch behavior”; `are-scanner-api-v1@v1.0` is unchanged.
- The implementation commit is `efa35f8db9647b32a1d5c5d73fead355910cae65`; canonical-master reachability was verified with `git merge-base --is-ancestor` (exit 0).
- No new score replay was substituted for the recorded run; this correction transcribes both recorded zero-spread series into the required standing schema.
- FAL audit: internal evidence only; every claim traces to the prior receipt, implementation commit, or canonical reachability check, with no external or coverage claim.
- Rollback: remove only this v0.2 receipt; retain v0.1 as superseded history.
