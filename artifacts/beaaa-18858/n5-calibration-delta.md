---
artifact_type: n5_calibration_delta
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-18858 proves the P1.5 accounting candidate preserves the frozen golden-set BackBond Score while correcting overlapping inconclusive evidence."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-18858", "BEAAA-18844"]
proof_points_cited: ["scripts/release_gate_n5.py", "artifacts/beaaa-18858/n5/report.json", "tests/test_p1_5_calibration_golden.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Candidate remains approval-gated as a rule-change preparation item; this PASS does not authorize merge, promotion, or a replacement Syllable score."]
---

# BEAAA-18858 N=5 calibration delta

BLUF: PASS. Against frozen scanner 0.5.0, the candidate has total delta `0`, every pillar delta `0` (floating noise below `3e-15`), and spread `0` across seeds 0..4 for every baselined golden fixture.

- `scanner_version`: `0.5.0` task baseline
- `corpus_version`: `registry-baseline-f794e63` for the Syllable task; golden release-gate corpus `0.2.0`
- `probe-set-version`: `p1_5_calibration_golden@0.1.0`
- `rubric_point_id`: `P1.5`
- N=5 seeds: `0,1,2,3,4`
- Golden roster: 8/9 baselined (`B2` remains excluded by the standing v1 decision)
- Maximum candidate total spread: `0`
- Maximum candidate pillar spread: `0`
- Maximum residual total delta: `0`
- Maximum residual pillar delta: less than `3e-15` floating noise
- Focused P1.5 suite: `9 passed`
- `cto_cosign_ref`: pending on BEAAA-18858 approval path

The P1.5 overlap regression independently proves the behavioral correction: old arithmetic yields `2.0` for 20 cases that are simultaneously correctness-inconclusive and confidence-unparseable; candidate set-union accounting yields `1.0`. Existing known-good and known-bad fixtures remain discriminative.

This receipt is internal and does not publish or replace a Syllable BackBond Score.
