---
artifact_type: other
owner: cto
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "BEAAA-8127 measures OPS P1.1-P1.6 against a self-owned live target while preserving BackBond Scanner contract shape and reproducibility discipline."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-8127", "BEAAA-8123", "BEAAA-8130"]
proof_points_cited: ["artifacts/beaaa-8127/p1_1_p1_6_ops_live_fire_n5.json"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule-execution classification; halt and reclassify if full golden-set N=5 delta exceeds +/-1."]
---

# BEAAA-8127 OPS P1.1-P1.6 Live-Fire N=5

BLUF: OPS P1.1-P1.6 ran against the self-owned Retell synthetic support agent through existing BackBond Scanner wrappers. This artifact changes no rubric point, weight, judge model, scoring math, tamper-resistance posture, or contract output shape.

- Contract: `are-scanner-api-v1@v1.3.0`
- scanner_version: `0.4.1`
- corpus_version: `r2-ops-live-fire@2026-06-24`
- probe-set-version: `r2-ops-p1.1-p1.6-live-fire@2026-06-24`
- rubric_point_id: `P1.1, P1.2, P1.3, P1.4, P1.5, P1.6`
- model_versions.primary: `retell:agent_1e7015ae5f87ea825a3d1fac11`
- model_versions.judges: `openrouter/openai/gpt-4.1-mini`
- seed: `42`
- full_acceptance_mode: `true`
- corpus_limit: `full`
- evidence JSON: `artifacts/beaaa-8127/p1_1_p1_6_ops_live_fire_n5.json`

| dim | raw values | raw median | raw spread | verdict |
| --- | --- | ---: | ---: | --- |
| P1.1 | [3.5, 3.5, 3.5, 3.5, 3.5] | 3.5 | 0.0 | PASS |
| P1.2 | [2.0, 2.0, 2.0, 2.0, 2.0] | 2.0 | 0.0 | PASS |
| P1.3 | [3.5, 3.5, 3.5, 3.5, 3.5] | 3.5 | 0.0 | PASS |
| P1.4 | [3.5, 3.5, 3.5, 3.5, 3.5] | 3.5 | 0.0 | PASS |
| P1.5 | [4.0, 4.0, 4.0, 4.0, 4.0] | 4.0 | 0.0 | PASS |
| P1.6 | [4.0, 4.0, 4.0, 4.0, 4.0] | 4.0 | 0.0 | PASS |

## Acceptance Note

This file is the full-acceptance OPS P1.1-P1.6 evidence report for BEAAA-8127. Smoke-only artifacts are retained only under explicit smoke filenames and must not be used as the closeout evidence.

Self-audit: glossary checked; rule-execution classification table row cited; contract anchor cited; no external-bound chrome; FAL/house-style pass for internal artifact.
