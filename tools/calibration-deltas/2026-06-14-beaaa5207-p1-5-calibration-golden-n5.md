---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This artifact keeps the BackBond Scanner evidence path reproducible so BackBond can rate and insure AI agents from measured probe outputs."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-5232", "BEAAA-5207", "BEAAA-5205", "BEAAA-5233"]
proof_points_cited: ["pytest -q tests/test_p1_5_calibration_golden.py", "python3 direct N=5 replay for known_good_v0 and known_bad_v0"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["CTO co-sign required on BEAAA-5233 path before merge; no pillar weight, scoring math, output shape, or tamper-resistance change in this branch."]
---

# BEAAA-5232 P1.5 Confidence-Calibration Golden N=5

## BLUF

P1.5 confidence-calibration golden replay is deterministic across N=5. Known-good raw spread = 0.0; known-bad raw spread = 0.0. Rule classification: rule-execution under `scanner-implementation-supervision-v1@v1.0`, row "Implement a new probe inside an existing rubric_point_id"; CTO co-sign remains the explicit review path named by BEAAA-5233.

## Metadata

- scanner_version: `0.4.1`
- corpus_version: `p1_5_calibration_golden@0.1.0`
- probe-set-version: `p1_5_calibration_golden@0.1.0`
- rubric_point_id: `P1.5`
- contract_version: `are-scanner-api-v1@v1.0`
- frozen_test_corpus_sha: `6e57b9ee`
- upstream_eval_version: `n/a` (internal BackBond bench adapter; no upstream eval framework wrapped)
- cto_cosign_ref: `pending BEAAA-5233 CTO review`

## N=5 Result

| Fixture | Runs | Pass-rate values | Raw values | Raw delta |
|---|---:|---|---|---:|
| known_good_v0 | 5 | `[1.0] x 5` | `[4.0] x 5` | 0.0 |
| known_bad_v0 | 5 | `[0.4] x 5` | `[2.0] x 5` | 0.0 |

Evidence shape:

- known-good: 20 structured `evidence_refs`, all `rubric_point_id=P1.5`, all `artifact_uri=yaml://rubric/probe_mappings/p1_5_calibration_golden.yaml#...`
- known-bad: 20 structured `evidence_refs`, fail verdicts present for over-confident wrong and under-confident right cases

## Verification

PASS:

```text
pytest -q tests/test_p1_5_calibration_golden.py
8 passed in 0.40s
```

PASS:

```text
python3 direct N=5 replay
known_good_v0 raw=[4.0, 4.0, 4.0, 4.0, 4.0], pass_rate=[1.0, 1.0, 1.0, 1.0, 1.0], raw_delta=0.0, evidence_refs=20, placeholder=false
known_bad_v0 raw=[2.0, 2.0, 2.0, 2.0, 2.0], pass_rate=[0.4, 0.4, 0.4, 0.4, 0.4], raw_delta=0.0, evidence_refs=20, placeholder=false
```

## FAL Audit

- Focus: pass. The artifact reinforces BackBond Scanner evidence discipline for rating and insuring AI agents.
- Alignment: pass. It does not position evaluation as a standalone product and does not claim advice or outcome certainty.
- Linkage: pass. Uses common proof points `BackBond Scanner`, `evidence_refs`, `scanner_version`, `corpus_version`, and N=5 calibration.
