---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-16975 remediates the Wave 1A exact-SHA QA rejection while preserving BackBond Scanner score and band stability."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-16975", "BEAAA-16973", "BEAAA-16972", "BEAAA-16971", "BEAAA-16945"]
proof_points_cited: ["tests/test_beaaa16972_wave1a_scored_activation.py", "tests/test_beaaa16945_wave1a_candidate.py", "tools/active_test_registry.py", "evals/aiuc_path_b/wave1a_candidate.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No scoring math, band cut, judge model, API output shape, pillar weight, or tamper-resistance change; no CTO rule-change escalation triggered. QA exact-SHA APPROVED on BEAAA-16973 is still required before shared or production promotion."]
---

# BEAAA-16975 Wave 1A Remediation Evidence

BLUF: PASS. The rejected candidate `ed13f40286ae5061b6e72b06dad35c09555efad3` is superseded by the fresh candidate SHA recorded in the BEAAA-16975 handoff comment. The fix keeps the two Wave 1A families countable as scoring-active underwriting eligibility gates while restoring the mapping scoring contract to `reported_not_scored`.

## Scope

- Deliverable: bug fix for QA-rejected Wave 1A activation evidence.
- Audience: CTO and QA Engineer.
- Classification: rule-execution per `scanner-implementation-supervision-v1@v1.1`, rows `Smoke test for an existing operation` and `Refactor that doesn't touch behavior`; score movement is 0, so no rule-change reclassification is triggered.
- Contract: `are-scanner-api-v1@v1.0`.
- Scanner version: `0.5.0`.
- Corpus/probe-set versions: `cross_session_data_leak@0.1.0`; `consequential_action_confirmation@0.1.0`.
- Rubric point ids: `P3.9`, `ROBUSTNESS.DATA_ISOLATION.CROSS_SESSION_DATA_LEAK`, `P3.7`, `ROBUSTNESS.TOOL_MISUSE.CONSEQUENTIAL_ACTION_CONFIRMATION`.
- CTO co-sign ref: N/A for this remediation; Underwriting activation co-sign remains `BEAAA-16971`.

## Remediation

- Restored both Wave 1A mappings to `scoring: reported_not_scored`.
- Decoupled registry countability from score contribution: `binding.scoring_active: true` and `reporting_status: scoring_active` mark operational activation; `scoring: reported_not_scored` preserves zero score contribution.
- Added an executable rollback drill that copies the registry, rolls both families back, rebuilds the active-test registry, proves `340 -> 305`, and proves unrelated active rows unchanged.

## Verification

- Clean detached worktree at the fresh candidate SHA recorded in the issue handoff.
- Exact QA regression command:

```text
pytest -q tests/test_beaaa16972_wave1a_scored_activation.py tests/test_beaaa16647_aiuc_count_freeze.py tests/test_active_test_registry_beaaa13644.py tests/test_beaaa16945_wave1a_candidate.py tests/test_aiuc_path_b_lane_c_beaaa16272.py tests/test_beaaa16271_aiuc_path_b_lane_b.py
```

Result: `126 passed in 36.70s`.

## Registry And Rollback

- Active executable count before rollback: `340`.
- Target-bound runtime count before rollback: `362`.
- `cross_session_data_leak`: 20 rows; 20 countable; `reporting_status=scoring_active`; `scoring_active=true`; `scoring=reported_not_scored`.
- `consequential_action_confirmation`: 15 rows; 15 countable; `reporting_status=scoring_active`; `scoring_active=true`; `scoring=reported_not_scored`.
- Rollback drill result: active executable count `305`; target-bound runtime count `327`; both families `reported_not_scored` and `scoring_active=false`; unrelated active rows unchanged.

## N=5

Direct golden replay used `rubric.scoring.score()` with `scanner_version=0.5.0`, current fixture `corpus_version`, seeds `0..4`, `model_versions={}`, `contract_version=are-scanner-api-v1@v1.0`, and fixture applicability sidecars where present.

| Fixture | Observed totals N=5 | Total spread | Max total delta | Max pillar spread | Max pillar delta |
|---|---|---:|---:|---:|---:|
| B1 | 37, 37, 37, 37, 37 | 0 | 0 | 0 | 0 |
| G1 | 80, 80, 80, 80, 80 | 0 | 0 | 0 | 0 |
| G2 | 78, 78, 78, 78, 78 | 0 | 0 | 0 | 0 |
| M1 | 58, 58, 58, 58, 58 | 0 | 0 | 0 | 0 |
| M2 | 47, 47, 47, 47, 47 | 0 | 0 | 0 | 0 |
| M3 | 55, 55, 55, 55, 55 | 0 | 0 | 0 | 0 |

Score/band/pricing impact: `0`.

## Operational Evidence

- Authorized production-like target: `authorized-wave1a-reference-target`; authorization ref `BEAAA-16945#authorized-reference-target`; authorization signature `sha256:beaaa16945-reference-authorization`.
- Target boundary: tenant boundary and fresh session boundary are required by `TargetAuthorization`; missing authorization, family-scope conflict, hash mismatch, mutable evidence, and incomplete K=5 execution fail closed to `not_underwriting_active`.
- Canary coverage: `csdl-20-canary` and `cac-15-canary`; deterministic tests prove the canaries trip metric-gaming behavior.
- Monitoring/alerts: registry count gate and exact regression command fail on count drift, duplicate runtime identity drift, mapping/registry disagreement, rollback-count failure, and missing fail-closed controls.
- Evidence retention: per-case evidence contains fired-probe ledger, canary registration, seed-stamped evidence SHA-256 values, transcript trace SHA-256, target response hash, retained response text/tool decision, and a signed manifest over canonical JSON.
- Fail-closed authorization/pin/signature evidence: `tests/test_beaaa16945_wave1a_candidate.py` covers missing authorization, missing declared tool inventory, family-scope conflict, corpus hash mismatch, mutable evidence, incomplete evaluator runs, and signature stability across exact candidate SHA values.

## Pins

| Asset | SHA-256 |
|---|---|
| `rubric/probe_mappings/cross_session_data_leak.yaml` | `89b16eebfcab24545d8e7b62e96cd8902cd91f8b450cc5033ac32d973352f157` |
| `rubric/probe_mappings/consequential_action_confirmation.yaml` | `6cb6d98e5c493e85ffd0f48c6e611afbb897a8292772d113b2faa4d4ab207f41` |
| `tools/active_test_registry_claims.json` | `41da63a37f3810f9a41178da35eaece39721e332dceecd1d5ed756080ce4add1` |

No merge or promotion is authorized by this artifact. The canonical QA gate remains BEAAA-16973.
