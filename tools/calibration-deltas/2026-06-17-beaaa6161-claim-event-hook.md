---
artifact_type: other
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This calibration record preserves the BackBond Scanner scoring boundary while claim candidate events join to bind/loss Scanner runs in Log Tracker v0."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1", "log-tracker-v0@v0.1.0", "log-event-schema-v1@v1.0.1"]
ticket_refs: ["BEAAA-6161", "BEAAA-6156", "BEAAA-6151"]
proof_points_cited: ["tools/log-tracker/tests/test_claim_event_write_hook.py", "tools/log-tracker/tests/acceptance_v0_2_calibration.py", "contracts/log-tracker-v0.md §8", "BEAAA-6173 UW semantic review", "BEAAA-6174 compliance review"]
version: 0.2
prior_version: "tools/calibration-deltas/2026-06-17-beaaa6161-claim-event-hook.md@v0.1"
self_audit_passed: true
escalations_noted: ["BEAAA-6173 requested a narrow claim_id semantics change; implemented by requiring a claim-management-system identifier instead of falling back to source signal id. BEAAA-6174 approved redaction-map sufficiency. No rule-change tripwire fired."]
---

# BEAAA-6161 N=5 Calibration Delta

**BLUF:** PASS. The Log Tracker v0 claim candidate write hook records `scan_id_at_bind` and `scan_id_at_loss` for synthetic claim fixtures and produced `Delta_total=0` and `Delta_subscore=0` across the N=5 golden set.

## Review Response

- BEAAA-6173 UW semantic review requested one narrow change: `claim_id` must not silently fall back to a detector/source signal id because BEAAA-6151 §3.3 defines `claim_id` as the claim-management-system identifier.
- Response: the post-bind claims queue builder now requires an explicit `claim_id`; the synthetic portal fixture supplies `synthetic-claim:<source_id>` at the adapter boundary instead of letting the sink substitute it.
- BEAAA-6174 approved the redaction-map posture with no code changes required.

## Classification

- Deliverable: write-hook implementation for UW / Head of Compliance review that causes claim candidate events to join to Scanner runs bracketing bind and loss.
- Classification: rule-execution under `scanner-implementation-supervision-v1@v1.1`, table row “Customer onboarding harness for a new customer archetype” analog / new evidence-pipeline path, as stated on BEAAA-6161 and BEAAA-6151 §9(e).
- Contract anchor: `are-scanner-api-v1@v1.0`; BackBond Scanner output shape unchanged.
- Rule-change tripwire: not fired. No scoring math, rubric point, judge model, corpus, pillar weight, or contract output shape changed.

## Required Operational Metadata

| Field | Value |
| --- | --- |
| scanner_version | `0.4.2` |
| corpus_version | `0.2.0` |
| probe-set-version | `log-tracker-v0-acceptance@0.2` |
| rubric_point_id | `n/a (tracker evidence ledger; no rubric points changed)` |
| N=5 calibration delta | `are_total=0`; `GOVERNANCE=0`; `ROBUSTNESS=0`; `BIAS_FAIRNESS=0`; `OPS=0` for each golden agent |
| upstream_eval_version | `n/a` — no upstream eval wrapper |
| cto_cosign_ref | `n/a` — rule-execution; UW + Head of Compliance review still required by issue scope |

## N=5 Acceptance Run

Command:

```bash
PYTHONPATH=tools/log-tracker/src python3 tools/log-tracker/tests/acceptance_v0_2_calibration.py --driver in-memory --json
```

Result:

| Metric | Value |
| --- | ---: |
| golden_agents | `B1`, `G1`, `G2`, `M1`, `M2`, `M3` |
| scanner_runs_recorded | 30 |
| evidence_refs_recorded | 750 |
| evidence_refs_expected | 750 |
| policy_events_recorded | 6 |
| claim_events_recorded | 6 |
| zero_artifact_uri_rollbacks | true |
| empty_db_sweeps_byte_identical | true |
| first_state_sha256 | `sha256:d71a98056354f0ff0410ce2a572ca40c80134506d65e2841758f6c89ab8454bb` |
| second_state_sha256 | `sha256:d71a98056354f0ff0410ce2a572ca40c80134506d65e2841758f6c89ab8454bb` |

Per-agent score deltas:

| Golden | `are_total_delta` | GOVERNANCE | ROBUSTNESS | BIAS_FAIRNESS | OPS |
| --- | ---: | ---: | ---: | ---: | ---: |
| B1 | 0 | 0 | 0 | 0 | 0 |
| G1 | 0 | 0 | 0 | 0 | 0 |
| G2 | 0 | 0 | 0 | 0 | 0 |
| M1 | 0 | 0 | 0 | 0 | 0 |
| M2 | 0 | 0 | 0 | 0 | 0 |
| M3 | 0 | 0 | 0 | 0 | 0 |

## Verification

```bash
pytest -q tools/log-tracker/tests/test_claim_event_write_hook.py tools/log-tracker/tests/test_policy_lifecycle_write_hook.py tools/log-tracker/tests/acceptance_v0_2_calibration.py tests/test_log_tracker_beaaa6159.py
25 passed in 3.70s
```

Additional compatibility run in the project artifact workspace:

```bash
pytest -q tools/post-bind-ingest/tests
32 passed in 0.28s

pytest -q tools/portal_v1/test_post_bind_sinks.py
11 passed in 44.06s
```

## FAL Audit

- Focus: pass. The hook supports BackBond’s rate-and-insure model by making claim evidence joinable to Scanner runs without turning the tracker into a score producer.
- Alignment: pass. No evaluation-as-product, governance-as-product, guarantee, carrier, or customer claim.
- Linkage: pass. Contract references and BEAAA tickets are explicit; canonical BackBond terminology used.
