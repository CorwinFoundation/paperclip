---
artifact_type: calibration_delta
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-15510", "BEAAA-15508"]
scanner_version: "0.4.1"
corpus_version: "2026.6.24"
probe_set_version: "runner@784778473d3e+plan-a-v0.2"
rubric_point_id: "batch1-immutable-selection"
cto_cosign_ref: null
self_audit_passed: true
---

# BEAAA-15510 N=5 Calibration Delta

BLUF: BEAAA-15510 changes the batch judge structured-output contract, parser, retry evidence, and fail-closed verdict mapping. It does not change BackBond Scanner scoring inputs, rubric weights, judge model choice, scored output shape, corpus contents, or tamper-resistance posture, so the golden-set scoring delta is zero for seeds 0-4.

- Contract: `are-scanner-api-v1@v1.0`
- Supervision contract: `scanner-implementation-supervision-v1@v1.1`
- Classification: rule execution - judge structured-output implementation under the approved BEAAA-15508 ADR
- Scanner: `0.4.1`
- Corpus: `2026.6.24`
- Probe set: `runner@784778473d3e+plan-a-v0.2`
- Immutable selection SHA-256: `710a49de1e5f1f394d611f9d71b3e1c41df52af03f3f2ade4cea7bc0f0b3d3a8`
- Seeds: `0, 1, 2, 3, 4`
- Release gate command: `python3 scripts/release_gate_n5.py --baseline 0.4.1 --candidate beaaa15510-fail-closed-judge --today 2026-07-18`
- Release gate artifact: `tools/calibration-deltas/2026-07-18-0.4.1-vs-beaaa15510-fail-closed-judge.md`
- Release gate result: PASS

| Seed | Total delta | Governance | Robustness | Bias & Fairness | Operations |
|---:|---:|---:|---:|---:|---:|
| 0 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 2 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 4 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

`cto_cosign_ref`: N/A. No rule-change threshold was crossed. No promotion is authorized; QA approval is still required before any shared or production promotion.
