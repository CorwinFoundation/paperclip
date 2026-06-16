# tools/post-bind-ingest — Phase 2.0 ingest gateway + normalizer

**Ticket:** BEAAA-5863 (parent BEAAA-5812).
**Authority:** ADR-0034 (`docs/adr/adr-0034-beaaa5815-phase-2-post-bind-log-architecture.md` @ `8d61761`).
**Binding contract:** Log Event Schema v1.0 (`contracts/log-event-schema-v1.md` @ `e19cfb9`).
**Supervision:** `scanner-implementation-supervision-v1@v1.1` row 17 — rule-execution.

## Scope (this package)

- Ingest gateway: push-mode signed-manifest receiver. Local-filesystem S3 substrate (`tests/fixtures/s3/`) for MVP; production wiring is Phase 2.0.1.
- Normalizer: raw vendor record → typed `LogEventSchemaV1Event` conforming to Schema v1.0.
- Redaction floor (BEAAA-5740 `SECRET_PATTERNS` from `tools/are-connector/src/are_connector/redaction.py`) — applied **before** any declared field class. Drift is caught by `test_redaction_drift_vs_connector.py`.
- Cross-event invariants 1–5 from Schema §Cross-event invariants.
- Defense-in-depth `score_invariants` publish (BEAAA-5761 pattern) — even though no scoring path is touched by this package.

## Out of scope

- Detector logic (P4 / P5 / stubs) — BEAAA-5865 / 5866.
- Sink emitters (claims queue / rescore feed / calibration feed) + observed-vs-predicted appendix — BEAAA-5867.
- Production S3 / KMS / IAM wiring, per-customer credential rotation — Phase 2.0.1 sub-child.
- Pull-mode (Phase 2.5) — separate Founder gate.
- `claim_candidate_event` emission — provenance `backbond_detector`; normalizer never emits it.
- Pn taxonomy reconciliation — BEAAA-5864 (does not block this ticket; normalizer never emits `claim_candidate_event`).

## Δ=0 attestation on `are-scanner-api-v1@v1.0`

By construction. No module under `src/post_bind_ingest/` imports `tools/portal_v1/`, `tools/are-connector/`, `src/scanner_v0_3_2/`, `api/`, `scanners/`, or `rubric/`. Enforced by `tests/test_no_score_imports.py`.

## Module layout

```
tools/post-bind-ingest/
  src/post_bind_ingest/
    ingest_gateway/         manifest receiver, HMAC signature, JSON Schema, idempotency, dead-letter
    normalizer/             raw vendor record → LogEventSchemaV1Event
      adapters/             per-vendor input shape (MVP: raw_stdout)
      event_families/       one module per Schema v1.0 event family (8 of 9; claim_candidate_event is detector-emitted)
    score_invariants.py     BEAAA-5761 defense-in-depth block (importable, schema-pinned)
  tests/                    10 required test modules per CTO brief §3
    fixtures/
      golden_events/        N=5 reproducibility fixture
      s3/                   local-FS S3 substrate
      signing_keys/         fixture-only HMAC keys
```

## Running tests

```
PYTHONPATH=tools/post-bind-ingest/src pytest tools/post-bind-ingest/tests -q
```

## Receipt

See `artifacts/beaaa-5863/closeout_receipt.md` for canon SHA, ADR-0034 cross-link, Schema v1.0 cross-link, N=5 determinism self-audit metadata, and Δ=0 attestation.
