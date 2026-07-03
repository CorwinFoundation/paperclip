---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-11366 adds an append-only runtime evidence store so BackBond Scanner runtime telemetry can support underwriting, determination, and actuarial reads without changing score rules."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-11366", "BEAAA-11362", "BEAAA-11341"]
proof_points_cited: ["BEAAA-11366 issue scope", "contracts/are-scanner-api-v1.md@v1.3.0"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# Evidence Store v1

BLUF: `migrations/0001_evidence_store_v1.sql` defines the append-only Postgres book of record for runtime evidence batches, structured summaries, and content hashes. It is additive infrastructure under `are-scanner-api-v1@v1.3.0`; it does not change BackBond Score math, rubric point weights, judge models, or scanner response shape.

## Write Path

1. A collector writes one `evidence_store.signed_batch_manifests` row per signed runtime batch. The row carries tenant identity, agent identity/version, scanner version, corpus version, probe set version, collector version, the signed manifest body, signature metadata, content hash, redaction metadata, consent metadata, and the retention policy key.
2. The collector writes one or more `evidence_store.structured_event_summaries` rows under that batch. Each row stores the structured runtime summary, event time, content hash, optional `rubric_point_id`, redaction metadata, and consent metadata.
3. The collector writes `evidence_store.evidence_content_hashes` rows for referenced content objects so downstream readers can verify content integrity without mutating the event summary.
4. Corrections are new inserts with `supersedes_evidence_batch_id` or `supersedes_evidence_event_id` plus `correction_reason`. Existing rows are never updated.
5. Batch corrections must stay inside the same `tenant_id_hash`, `agent_id`, and `agent_version` lineage as the superseded batch. Event corrections must stay inside the same tenant, agent, agent version, and `evidence_batch_id` lineage as the superseded event.
6. Each superseded batch or event may have exactly one direct correcting child. A later correction appends to the prior correction, producing a single-head revision chain instead of sibling current rows.

## Read Model

Downstream determination and actuarial consumers read:

- `evidence_store.latest_signed_batch_manifests` for current batch state.
- `evidence_store.latest_structured_event_summaries` for current event summaries.
- `evidence_store.exposure_meterable_events_v1` for the normalized covered-action event projection.
- `evidence_store.exposure_metering_v1` for monthly covered-action counts by tenant, agent, and action class.
- Base tables for full chain-of-custody and correction history.

The latest views hide superseded rows from current-state reads while preserving historical rows for claims and audit reconstruction. The one-direct-child constraint keeps each logical batch or event chain to one current head.

Exposure Metering v1 is read-only. It counts only current, non-superseded structured evidence rows, derives `action_class` from `summary.action_class`, `summary.attributes["backbond.evidence.action_class"]`, or `event_kind`, and carries `evidence_event_ids` plus `source_evidence_row_count` so billing and actuarial reads reconcile back to the runtime evidence book. Missing in-range monthly periods emit `metering_status = 'unmetered'` with `covered_action_count = NULL`; that is intentionally distinct from observed zero activity.

## Retention Stub

`evidence_store.retention_config` ships one explicit stub row: `runtime_evidence_counsel_pending_v1`. `retention_period` and `legal_hold_after` are `NULL` until counsel provides retention values. New counsel-approved retention values should be inserted as a new row with a new `retention_policy_key`; existing rows are protected by the same append-only triggers.

## Append-Only Semantics

The migration installs a shared trigger function and `BEFORE UPDATE` / `BEFORE DELETE` triggers on every book-of-record table. Destructive edits fail with `evidence_store_append_only_update_forbidden` or `evidence_store_append_only_delete_forbidden`.

Operational metadata for BEAAA-11366:

- `scanner_version`: unchanged, additive runtime evidence persistence only.
- `corpus_version`: unchanged.
- `probe-set-version`: unchanged.
- `rubric_point_id`: no rubric point changed; event summaries may optionally reference existing IDs.
- `N=5 calibration delta`: not expected to move score output; verify with the closeout calibration command before merge.
- `cto_cosign_ref`: not required; classified rule-execution per `scanner-implementation-supervision-v1@v1.1` row "Refactor that doesn't touch behavior" / evidence-pipeline glue under existing rules.

Operational metadata for BEAAA-11369:

- `scanner_version`: unchanged, additive evidence-store DDL hardening only.
- `corpus_version`: unchanged.
- `probe-set-version`: unchanged.
- `rubric_point_id`: no rubric point changed; DDL protects runtime evidence lineage only.
- `N=5 calibration delta`: not expected to move score output; verify with the L1 golden gate before merge.
- `cto_cosign_ref`: not required; classified rule-execution per `scanner-implementation-supervision-v1@v1.1` table rows "Smoke test for an existing operation" and "Refactor that doesn't touch behavior"; no BackBond Score math, rubric, judge, or `are-scanner-api-v1@v1.3.0` response-shape change.

Operational metadata for BEAAA-11373:

- `scanner_version`: unchanged, additive read-only exposure metering over runtime evidence persistence only.
- `corpus_version`: unchanged.
- `probe-set-version`: unchanged.
- `rubric_point_id`: no rubric point changed; metering reads runtime evidence rows without changing score rules.
- `N=5 calibration delta`: no score path touched; verify with the smallest available golden gate before close.
- `cto_cosign_ref`: not required; classified rule-execution per `scanner-implementation-supervision-v1@v1.1` rows "Smoke test for an existing operation" and "Refactor that doesn't touch behavior"; no BackBond Score math, rubric, judge, tamper-resistance reduction, or `are-scanner-api-v1@v1.3.0` response-shape change.
