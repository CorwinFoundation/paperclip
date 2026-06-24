---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-8129 records that the P2.2 candidate corpus packet did not wire a score path and therefore cannot be reported as measured until Underwriter sign-off plus N=5 live calibration."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-8129", "BEAAA-8123"]
proof_points_cited:
  - "artifacts/beaaa-8129/manifest.json"
  - "artifacts/beaaa-8129/measurement_spec.md"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Underwriter sign-off is required before score-bearing P2.2 use."
---

# N=5 Calibration Delta Note — BEAAA-8129 P2.2 Candidate Corpus

## BLUF

No N=5 score delta was run in this heartbeat because BEAAA-8129 produced a candidate labeled-outcome corpus only. No scanner score path, scoring math, judge model, probe runner, or output shape changed; reporting P2.2 as measured remains blocked until Underwriter signs the corpus and the live probe path passes N=5 calibration.

## Required Operational Metadata

| Field | Value |
|---|---|
| `scanner_version` | `0.4.1` target, unchanged by this packet |
| `corpus_version` | `p2_2_equal_opportunity_labeled_outcome_v0.1_candidate` |
| `probe-set-version` | `bbq-holisticbias-phase1-v0.1.0 + p2_2_equal_opportunity_candidate_v0.1` |
| `rubric_point_id` | `P2.2` |
| `N=5 calibration delta` | Not applicable at corpus-candidate stage; required before any merge that makes P2.2 measured. |
| `cto_cosign_ref` | Not required at corpus-candidate stage; required if live wiring creates >1 golden delta or changes scoring rules. |

## Closure Guard

Do not close a future P2.2 live-wiring ticket unless it includes:

- N=5 seeds `0..4`
- per-agent total and pillar deltas
- evidence refs with `are-probe://bias-fairness/p2.2/equal-opportunity-gap/...`
- `scanner_version`, `corpus_version`, `probe-set-version`, `rubric_point_id`, and model pins
- Underwriter sign-off reference for the corpus
