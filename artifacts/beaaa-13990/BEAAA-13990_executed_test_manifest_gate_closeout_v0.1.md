---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This correction keeps BackBond Scanner executed-test claims tied to complete per-case evidence before any 1,180-test claim can be restored."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-13990", "BEAAA-13915", "BEAAA-13762"]
proof_points_cited: ["tools/canonical_test_registry.py", "tests/test_canonical_test_registry_beaaa13762.py", "artifacts/beaaa-13990/canonical-registry-executed-gate-2026-07-13.json"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No escalation gate triggered: metadata/count verifier only; no scoring math, rubric weight, judge model, corpus content, API shape, or tamper-resistance change."]
---

# BEAAA-13990 Executed-Test Manifest Gate Closeout

BLUF: BEAAA-13990 is closed as rule execution. The registry can still track active executable candidates, but a case is not countable as an executed test unless the manifest row carries the complete seven-part execution-evidence gate.

## Change

- Added `PUBLIC_EXECUTED_TEST_COUNT = 90` to `tools/canonical_test_registry.py`.
- Added `countable_as_executed_test` validation that rejects counted rows missing any of:
  `prompt_or_action`, `expected_behavior`, `target_response`, `verdict`, `verdict_provenance`, `manifest_evidence`, `github_provenance`.
- Emitted `artifacts/beaaa-13990/canonical-registry-executed-gate-2026-07-13.json`.

## Verification

- `python3 -m pytest tests/test_canonical_test_registry_beaaa13762.py -q` -> `10 passed in 28.14s`.
- `python3 -m tools.canonical_test_registry --repo . --emit artifacts/beaaa-13990/canonical-registry-executed-gate-2026-07-13.json --check` -> exit 0.

## Required Metadata

| Field | Value |
| --- | --- |
| scanner_version | `1.2.0` |
| corpus_version | `scanner-test-registry-baseline-2026-07-13` |
| probe-set-version | `scanner-test-registry-baseline-2026-07-13/v0.1` |
| rubric_point_id | `ALL_ACTIVE_DIMENSIONS` |
| N=5 calibration delta | `not_applicable_metadata_only_no_score_path_change` |
| upstream_eval_version | `not_applicable` |
| cto_cosign_ref | `not_required_rule_execution` |

## Disposition

Public executed-test count remains **90**. The BEAAA-13915 expanded active registry snapshot must not be represented as executed-test breadth until each counted case satisfies the verifier.
