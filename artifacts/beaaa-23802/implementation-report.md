---
issue: BEAAA-23802
artifact_type: implementation_report
persona_audience: internal
scanner_version: 0.5.0
rubric_version: 1.3.0
corpus_version: per-fixture persisted pins; no corpus mutation
probe_set_version: unchanged
rubric_point_id: golden-checkpoint-enforcement
n5_calibration_delta: artifacts/beaaa-23802/n5-calibration-delta.json
cto_cosign_ref: BEAAA-23800; BEAAA-23801
contract_version: are-scanner-api-v1@v1.3.0
---

# ADR-0067 v1.1 aligned checkpoint implementation

## BLUF

The additive rubric-1.3.0 checkpoint now enforces all eight approved fixtures without changing historical baseline bytes. The comparator fails closed for absent or unapproved aligned checkpoints, masked fixtures, missing pins, pin mismatches, and score movement outside the approved total or pillar bands.

## Classification and implementation insight

This is rule execution under the approved ADR-0067 v1.1 architecture and the BEAAA-23800 / BEAAA-23801 scoring-governance decisions. It stores the approved checkpoint separately, keeps the old lineage read-only, derives caveats from the checkpoint record, and preserves B1's empty corpus pin as an intentional value.

## Verification

- Focused acceptance and existing calibration wiring: 12 tests passed.
- N=5 deterministic replay: zero total and pillar delta for seeds 0 through 4 across B1, B3, G1, G2, G3, M1, M2, and M3.
- Historical byte identity is asserted for every `artifacts/beaaa-387/*/baseline.json` file.
- FAL audit: purpose, source commit, approvals, caveats, contract version, pins, rollback, and limitations are explicit. No external factual claim is introduced.

## Rollback

Remove the additive comparator and checkpoint files. Historical lineage remains untouched, and callers return `BLOCKED_NO_ALIGNED_BASELINE` until another approved checkpoint is available.
