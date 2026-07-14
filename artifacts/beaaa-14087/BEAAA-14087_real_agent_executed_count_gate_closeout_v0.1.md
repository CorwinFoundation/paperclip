---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-14087 prevents fixture and local-harness evidence from inflating the BackBond Scanner real-agent executed count."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-14087", "BEAAA-13915", "BEAAA-13991", "BEAAA-14012", "BEAAA-14013", "BEAAA-14014", "BEAAA-14015", "BEAAA-14016"]
proof_points_cited: ["tools/real_agent_execution_gate.py", "tools/canonical_test_registry.py", "tests/test_canonical_test_registry_beaaa13762.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No CTO co-sign gate triggered: rule-execution count-gate hardening, no rubric weight, scoring math, judge model, corpus semantics, BackBond Scanner API shape, or tamper-resistance reduction."]
---

# BEAAA-14087 Real-Agent Executed-Count Gate Closeout v0.1

BLUF: BEAAA-14087 is implemented. `executed_agent_test_count` remains **90**; the invalid wave rows add **0** to the real-agent count and are reported separately as quarantined evidence.

This is a harness/count-gate hardening change for CTO/Auditor that prevents manifest assertions, local targets, mocks, fixtures, terminal-only assertions, and non-empty-response checks from counting as real-agent BackBond Scanner executions within one engineering heartbeat.

## Classification

Rule-execution per `scanner-implementation-supervision-v1@v1.1`: metadata/count-gate hardening for existing evidence rows. No scoring math, rubric weights, judge models, corpus semantics, API output shape, or customer-data handling changed.

## Gate Implemented

A row can increment `executed_agent_test_count` only when all of the following are true:

- `target_type` is `real_agent`.
- `target_identity` matches the approved authenticated demo/customer agent allowlist.
- Raw request and raw response evidence are both present.
- Raw request and response identity match the allowlisted target identity.
- The raw request carries an authentication proof hash.
- Evidence text does not contain localhost, `127.0.0.1`, mock, echo, replay, deterministic fixture, generated `LOCAL_TARGET_RESPONSE`, prewritten response, terminal-only, or non-empty-response assertion markers.

## Quarantine Result

| Metric | Result |
|---|---:|
| `public_executed_test_count` | 90 |
| `executed_agent_test_count` | 90 |
| Invalid wave rows quarantined from additional real-agent count | 537 |
| Additional count from BEAAA-13991 / 14012 / 14013 / 14014 / 14015 / 14016 | 0 |

The quarantine ledger is emitted as `invalid_wave_quarantine_ledger` by `tools.canonical_test_registry.build_registry`.

## Verification

`python3 -m pytest tests/test_canonical_test_registry_beaaa13762.py -q`

Result: `14 passed, 10 subtests passed in 26.33s`.

Registry spot check:

- `executed_agent_test_count`: 90
- `public_executed_test_count`: 90
- `invalid_wave_quarantined_additional_count`: 537
- registry errors: none

## Required Scanner Metadata

| Field | Value |
|---|---|
| scanner_version | 1.2.0 |
| corpus_version | scanner-test-registry-baseline-2026-07-13 |
| probe-set-version | scanner-test-registry-baseline-2026-07-13/v0.1 |
| rubric_point_id | ALL_ACTIVE_DIMENSIONS |
| N=5 calibration delta | not_applicable_metadata_count_gate_only_no_score_path_change |
| cto_cosign_ref | not_required_rule_execution |

## Rollback

Revert the BEAAA-14087 count-gate commit. That restores the previous generic execution evidence gate, but the invalid 1,180 real-agent count must not be restored without CTO approval.
