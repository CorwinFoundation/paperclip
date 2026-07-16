---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-14087 prevents fixture and local-harness evidence from inflating the BackBond Scanner real-agent executed count."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-14087", "BEAAA-13915", "BEAAA-13991", "BEAAA-14012", "BEAAA-14013", "BEAAA-14014", "BEAAA-14015", "BEAAA-14016"]
proof_points_cited: ["tools/real_agent_execution_gate.py", "tools/canonical_test_registry.py", "tests/test_canonical_test_registry_beaaa13762.py", "tools/calibration-deltas/2026-07-15-beaaa-14087-baseline-vs-candidate-n5.md"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No CTO co-sign gate triggered: rule-execution count-gate hardening, no rubric weight, scoring math, judge model, corpus semantics, BackBond Scanner API shape, or tamper-resistance reduction."]
---

# BEAAA-14087 Real-Agent Executed-Count Gate Closeout v0.1

BLUF: BEAAA-14087 is implemented on a clean branch based on canonical master `dd37992`. `executed_agent_test_count` is now derived only from authenticated evidence rows and fails closed to **0** because the repository contains no evidence-backed baseline manifest; the legacy public audited metric remains separately reported as **90**.

This is a harness/count-gate hardening change for CTO/Auditor that prevents manifest assertions, local targets, mocks, fixtures, terminal-only assertions, and non-empty-response checks from counting as real-agent BackBond Scanner executions within one engineering heartbeat.

## Classification

Rule-execution per `scanner-implementation-supervision-v1@v1.1`: metadata/count-gate and verifier-composition hardening for existing evidence rows. No scoring math, rubric weights, judge models, corpus semantics, API output shape, or customer-data handling changed. Tamper resistance is strengthened, not reduced.

## Gate Implemented

A row can increment `executed_agent_test_count` only when all of the following are true:

- `target_type` is `real_agent`.
- `target_identity` resolves in a server-owned control-plane approval map.
- Raw request and raw response evidence are both present.
- The raw request HTTPS origin matches the control-plane approval.
- The execution authority's HMAC signature authenticates the case id, target identity, request URL, request digest, and response digest. Caller-provided labels or hashes alone never authenticate evidence.
- Evidence text does not contain localhost, `127.0.0.1`, mock, echo, replay, deterministic fixture, generated `LOCAL_TARGET_RESPONSE`, prewritten response, terminal-only, or non-empty-response assertion markers.
- Agent-countable rows also pass the general executed-evidence contract; agent eligibility is a strict subset of executed eligibility.
- Production CLI composition requires both a server-owned approval manifest and an evidence manifest. Approval HMAC keys are loaded from named environment variables, never from evidence rows or committed configuration.
- Approval origins must be canonical public HTTPS origins, malformed ports reject without raising, and HMAC keys must contain at least 32 bytes.

## Quarantine Result

| Metric | Result |
|---|---:|
| `public_executed_test_count` | 90 |
| `executed_agent_test_count` | 0 |
| Invalid wave rows present in the current repository and quarantined | 0 |
| Additional count from BEAAA-13991 / 14012 / 14013 / 14014 / 14015 / 14016 | 0 |

The registry rewrites any claimed executed-agent row that fails verification to `countable_as_executed_agent_test: false` before validation, ledger emission, or counting. The total is the deduplicated cardinality of authenticated evidence rows only. Named invalid-wave quarantine metadata is row-bound through `ticket_ref` / `ticket_refs`; absent rows are not fabricated into the ledger.

## Verification

`python3 -m pytest -q tests/test_canonical_test_registry_beaaa13762.py`

Result: `23 passed, 10 subtests passed`.

Mandatory N=5 gate: `python3 scripts/beaaa6190_v05_rc_n5.py` PASS. All six live golden agents were byte-identical to their expected outputs; total spread was `0` and maximum pillar delta was `0.0000`. Three roster slots remain explicitly pending in the frozen golden fixture.

The dated, immutable comparison artifact is `tools/calibration-deltas/2026-07-15-beaaa-14087-baseline-vs-candidate-n5.md`; it records total plus GOV, ROB, BIAS, and OPS deltas for each live agent.

| Golden agent | Baseline score | Candidate score | Score delta | Baseline max pillar spread | Candidate max pillar spread |
|---|---:|---:|---:|---:|---:|
| B1 | 37 | 37 | 0 | 0.0000 | 0.0000 |
| G1 | 80 | 80 | 0 | 0.0000 | 0.0000 |
| G2 | 78 | 78 | 0 | 0.0000 | 0.0000 |
| M1 | 58 | 58 | 0 | 0.0000 | 0.0000 |
| M2 | 47 | 47 | 0 | 0.0000 | 0.0000 |
| M3 | 55 | 55 | 0 | 0.0000 | 0.0000 |

`B2`, `B3`, and `G3` are pending in both baseline and candidate frozen fixtures and therefore have no score delta.

Registry spot check:

- `executed_agent_test_count`: 0
- `public_executed_test_count`: 90
- `invalid_wave_quarantined_additional_count`: 0
- registry errors: none

## Required Scanner Metadata

| Field | Value |
|---|---|
| scanner_version | 1.2.0 |
| corpus_version | scanner-test-registry-baseline-2026-07-13 |
| probe-set-version | scanner-test-registry-baseline-2026-07-13/v0.1 |
| rubric_point_id | ALL_ACTIVE_DIMENSIONS |
| N=5 calibration delta | PASS; total spread 0; max pillar delta 0.0000; 6 live byte-identical, 3 pending |
| cto_cosign_ref | not_required_rule_execution |

## GitHub Provenance

| Field | Value |
|---|---|
| Base | `origin/master@dd37992` |
| Head | PR `#73` branch `beaaa-14087-real-agent-count-gate`; exact immutable SHA recorded in the issue delivery comment |
| Pull request | `#73` |
| Rebase result | Clean; no conflicts |
| Focused verification | `23 passed, 10 subtests passed` |

## Rollback

Revert the BEAAA-14087 count-gate commit. That restores the previous generic execution evidence gate, but the invalid 1,180 real-agent count must not be restored without CTO approval.
