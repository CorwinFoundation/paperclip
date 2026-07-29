---
artifact_type: calibration_delta
owner: scanner_engineer_3
persona_audience: internal
canonical_terms_only: true
ticket_refs: ["BEAAA-18882"]
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
scanner_version: "0.5.0"
corpus_version: "0.2.0"
probe-set-version: "oss007-deepteam-bfla-bola@0.1.0"
rubric_point_id: ["ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL", "ROBUSTNESS.DATA.CROSS_CUSTOMER_EXPOSURE"]
cto_cosign_ref: null
self_audit_passed: true
---

# BEAAA-18882 N=5 calibration delta

BLUF: PASS. The deterministic OSS-007 discovery fixtures are reported-not-scored and move every golden total and pillar by exactly 0.

- Classification: rule execution, supervision-table row “Wrap an upstream eval framework as a BackBond Scanner probe.”
- Immutable scoring comparison: baseline `7b0d5f7711083c9d3366b9851cc8d309145bfa33`; hardened implementation candidate `c28b10b3fb0b303f332249ac3c5ef61f81688a35`.
- Golden replay: seeds 0–4, 8/9 baselined fixtures (G1/G2/G3/M1/M2/M3/B1/B3); B2 excluded by the existing approved v1 gate.
- Result: candidate spread 0; baseline-to-candidate delta 0 for total, GOVERNANCE, ROBUSTNESS, BIAS_FAIRNESS, and OPS on every replayed fixture.
- Gate command: `python3 scripts/release_gate_n5.py --baseline 7b0d5f7 --candidate c28b10b3fb0b303f332249ac3c5ef61f81688a35 --phase development --today 2026-07-29`.
- Mock suite: `55 passed, 36 subtests passed` in 1.66 seconds.
- QA defect disposition: candidate `efa4f5e165907bd2471999917f4018270cd3b4da` is superseded; empty and malformed tool/authorization metadata now fail closed to `not_applicable`.
- Upstream taxonomy pin: DeepTeam commit `e05c8e5cdb3467f188fa43147991becd328e5228`.
- FAL audit: pass — isolated evidence-only discovery expands coverage without changing the product focus, scoring contract, or canonical counts.
- Rollback: remove `evals/oss007/` and its targeted test; no registry, corpus, rubric, score, or API state changes.
