---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-17732 adds target-produced governance/eval evidence for three BackBond Scanner control families without score activation."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-17732"]
proof_points_cited: ["tools/governance_eval_target_runner.py", "tests/test_beaaa17732_governance_eval_target_runner.py", "artifacts/beaaa-17732/target-smoke/summary.json"]
version: "0.1"
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule-execution implementation: existing rubric_point_id families, no score activation, no scoring math, no judge model, no contract output shape, no tamper-resistance reduction."]
---

# BEAAA-17732 Implementation Closeout

BLUF: Implemented the canonical governance/eval target runner for ordinals 10, 11, and 20. The runner invokes one configured non-customer target path per family and accepts PASS only from target-produced evidence, not catalogue presence.

Deliverable: This is a target-runner implementation for the CTO that closes the ordinals 10/11/20 execution gap inside the existing BEAAA-17667 family activation ledger scope within the 8-hour, 30-call, $5 budget.

Classification: rule-execution under `scanner-implementation-supervision-v1@v1.1`, row "Refactor/implementation that doesn't touch behavior" plus existing-rubric probe/control execution. No new rubric point, weight, scoring math, judge model, contract output shape, or tamper-resistance posture change.

Contract anchor: `contracts/are-scanner-api-v1.md@v1.0` filename grandfathered; active scanner contract file currently states v1.3.0. This implementation does not change `/scanner/v1/scans`, `/scanner/v1/rescans`, scoring output shape, or score activation.

Implementation:
- Added `tools/governance_eval_target_runner.py`.
- Added `tests/test_beaaa17732_governance_eval_target_runner.py`.
- Families: `eval_coverage_breadth`, `eval_representation`, `mitigation_evidence`.
- Immutable pins: `scanner_version=0.5.0`, `corpus_version=canonical-61-family-registry@2026-07-13`, `probe-set-version=family-activation-ledger/v0.1`.
- Verdicts: explicit `PASS`, `FAIL`, `INVALID`.
- Evidence hashes: request/config/input/output/verdict hashes plus content-addressed evidence record SHA-256.

Verification:
- `pytest -q tests/test_beaaa17732_governance_eval_target_runner.py tests/test_beaaa17667_family_activation_ledger.py`
- Result: `13 passed in 3.43s`.

Bounded target smoke:
- Target config class: `local_http_test_fixture`.
- Target identity: `beaaa-17732-local-http-test-target`.
- Credential class: `test_only`.
- Call cap / actual calls: `30 / 3`.
- Spend cap / actual spend: `$5.00 / $0.00`.
- Customer data: `false`.
- Production credentials: `false`.
- Score activation: `false`.
- Summary: `artifacts/beaaa-17732/target-smoke/summary.json`.
- Summary SHA-256: `38688c71fb9e666a93d294743fec9ffe9a5031951b9ed742b5da89977ba3461a`.

N=5 calibration delta:
- Status: not run as score-bearing calibration because this change does not enter a score-bearing path and the smoke has `score_activation=false`.
- Expected score movement: `0`.
- Required if score-bearing path is later wired: seeds 0-4, total and every pillar within +/-1 before merge.

QA gate:
- Required before reporting these rows working: QA Engineer exact-SHA `APPROVED` on the canonical QA child for BEAAA-17732.
- Current status at artifact creation: pending QA child creation for the immutable candidate SHA.

Self-audit:
- Read required canon and Scanner contract files this turn.
- Glossary clean for new artifact text.
- No external-bound asset; Brand Backbone gate not required.
- FAL audit: Focus linked to BackBond Scanner evidence; aligned with score/evidence execution; linkage via BEAAA-17732 and the canonical 61-family registry.
