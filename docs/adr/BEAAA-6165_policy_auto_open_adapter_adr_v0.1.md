---
artifact_type: adr
owner: scanner_engineer
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "This adapter lets BackBond open claim intake records from telemetry or customer notice without turning scanner evidence into automatic coverage, reserve, or payment decisions."
contracts_referenced: ["are-scanner-api-v1", "telemetry-api-v0"]
ticket_refs: ["BEAAA-6165", "BEAAA-6155", "BEAAA-6152", "BEAAA-5817"]
proof_points_cited:
  - "BEAAA-6152 claims auto-open trigger spec v0.1"
  - "contracts/telemetry-api-v0.md §5.5 claims_trigger_envelope"
  - "tools/post-bind-ingest/src/post_bind_ingest/sinks/claims_queue.py threshold_policy_governed P4/P6 metadata"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "Scope-stretch acknowledged on BEAAA-6165: policy-system code implemented under CTO one-off supervision."
  - "HoUW wording-section live lookup remains open; MVP uses importable stub labels only."
  - "Workspace root is not a Git repository, and canonical are-scanner repo does not contain tools/portal_v1; merged_sha_on_canon_master_at_close cannot be populated until CTO/harness provides the merge target."
  - "Artifact metadata schema owner enum does not include scanner_engineer, but existing Scanner Engineer artifacts use this owner."
---

# BEAAA-6165 - Policy-System Auto-Open Adapter ADR v0.1

## BLUF

Implemented the BEAAA-6152 MVP as a SQLite-backed policy-system adapter attached to Portal v1, with one internal `open_claim_record(auto_open_event)` write path for telemetry threshold events and customer loss signals. The write path always creates `intake_open` records with `coverage_state=unreviewed`, `reserve_state=not_set`, and `payment_state=not_approved`; it does not mutate BackBond Scanner output under `are-scanner-api-v1@v1.0`.

## Deliverable

This is a policy-system adapter and endpoint implementation for CTO that will open MVP claim-intake records from BEAAA-6152 telemetry and customer-loss triggers inside the Portal v1 harness with no new external spend.

## Classification

Policy-system scope-stretch under BEAAA-6165. Scanner supervision fields `rubric_point_id`, `probe-set-version`, `N=5 calibration delta`, and `cto_cosign_ref` are N/A per the issue description. By analogy to `contracts/scanner-implementation-supervision-v1.md@v1.1`, this is rule execution: customer onboarding / evidence-pipeline glue under locked BEAAA-6152 behavior, not scoring math, judge model, rubric, contract shape, or tamper-resistance change.

## Decision

Use an additive SQLite MVP store in Portal v1:

- `claim_records` persists the BEAAA-6152 minimum record field set plus the spec-allowed customer `summary`.
- `claim_record_activity` stores sanitized duplicate attachments: source id/type, evidence pointers, customer reference ids, and summary only.
- `intake_audit_events` records every refused open with rejection reason, source event id, policy id when known, and timestamp.

Customer-loss auth posture for MVP: `synthetic-policy:{dp_id}` resolves only to an enrolled design partner in the portal harness, and `reporter_email` must match that enrolled email. This is test-environment allow-list auth, matching the BEAAA-6165 scope; production-grade auth remains out of scope.

## Implementation Notes

Files changed:

- `tools/portal_v1/policy_auto_open_v0_1.py`
- `tools/portal_v1/portal.py`
- `tools/portal_v1/test_policy_auto_open.py`

The adapter exposes:

- `open_claim_record(auto_open_event)`
- `normalize_claims_queue_item(...)`
- `normalize_claims_trigger_envelope(...)`
- `handle_customer_loss_signal(policy_id, payload)`
- `POST /api/v1/policies/{policy_id}/claim-intake/customer-loss-signal`
- `GET /api/v1/policies/{policy_id}/claim-records/{claim_record_id}`

Duplicate suppression follows the 24-hour fingerprint rules:

- telemetry: `telemetry:{policy_id}:{source_signal_envelope_id}`
- customer: `customer:{policy_id}:{customer_loss_signal_id}`
- customer fallback helper implemented for payloads without an id, though BEAAA-6165 endpoint acceptance requires `customer_loss_signal_id`.

Wording section candidates are loaded through importable `lookup_wording_section_candidates(...)`. The MVP stub maps `P4` and `P6` to label strings only and does not hard-code wording section numbers.

## Verification

Command:

```bash
python3 -m pytest tools/portal_v1/test_policy_auto_open.py tools/portal_v1/test_post_bind_sinks.py -q
```

Result:

```text
21 passed in 72.14s (0:01:12)
```

Evidence by scenario:

- Scenario 1: `test_scenario_1_telemetry_threshold_opens_record` passed. Created one telemetry-triggered `claim_record` with `status=intake_open`, `coverage_state=unreviewed`, `reserve_state=not_set`, `payment_state=not_approved`, `trigger_source=telemetry_threshold`, and governed `threshold_policy_ref=BEAAA-5817_v0.3`.
- Scenario 2: `test_scenario_2_customer_loss_signal_endpoint_opens_record` passed. HTTP POST customer loss signal created one `claim_record` with `trigger_source=customer_loss_signal` and `intake_class=claim_notice`.
- Scenario 3: `test_scenario_3_duplicate_within_24h_suppresses_telemetry_and_customer` passed. Duplicate telemetry and customer submissions returned `duplicate_suppressed`, did not create second records, and appended activity rows to the existing records.

Additional guard checks passed:

- `test_normalizers_feed_exact_auto_open_event_field_set`
- `test_customer_loss_signal_feeds_exact_auto_open_event_field_set`
- `test_claims_trigger_envelope_normalizes_to_same_write_path`
- `test_customer_loss_signal_informational_only_opens_incident_watch`
- `test_customer_loss_rejection_writes_intake_audit_event`
- `test_claim_record_guards_and_source_hygiene`
- `test_adapter_source_does_not_hard_code_wording_section_numbers`

Adjacent regression:

- `tools/portal_v1/test_post_bind_sinks.py` remained green in the combined run, proving existing claims queue and observed-vs-predicted sink behavior still works after service attach changes.

## Scanner Context Anchors

- `scanner_version`: `0.4.1`
- `corpus_version`: `0.2.0`
- `are-scanner-api-v1`: `v1.0`
- `telemetry-api-v0`: referenced for `claims_trigger_envelope`

## FAL Audit

Pass. Focus: the artifact reinforces BackBond as rating-and-insurance infrastructure by turning telemetry/customer notice into controlled claim intake. Alignment: it explicitly avoids evaluation-as-product, governance-as-product, automatic coverage, reserve, or payment promises. Linkage: uses the BackBond Scanner / BackBond Score contract anchor and BEAAA-6152 claims evidence path.

## Open Items

1. HoUW to replace the MVP wording-section stub with the live wording schedule lookup.
2. CTO or harness owner to provide the canonical policy-system merge target. Current workspace root has no `.git`, and canonical `/mnt/paperclipdata/dot-paperclip/instances/default/companies/59f8876e-e729-4dda-98f9-1317c2b50492/projects/are-scanner` does not contain `tools/portal_v1`, so `merged_sha_on_canon_master_at_close` cannot be truthfully populated from this heartbeat.

## Closeout State

Implementation and tests are complete in this workspace. The issue is not contract-closeable as `done` until the merge target is clarified and merge reachability can be verified.
