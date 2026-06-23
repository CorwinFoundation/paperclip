# tools/post-bind-ingest — BackBond Phase 2.5 receiver boundary

**Ticket:** BEAAA-5968 (parent BEAAA-5812).
**Authority:** ADR-0034 (`docs/adr/adr-0034-beaaa5815-phase-2-post-bind-log-architecture.md` @ `8d61761`).
**Binding contract:** Log Event Schema v1.0 (`contracts/log-event-schema-v1.md` @ `e19cfb9`).
**Current issue:** BEAAA-5968 (`beaaa` branch migration to BackBond-side egress receiver).
**Supervision:** `scanner-implementation-supervision-v1@v1.1` row 17 — rule-execution.

## Scope (this package)

- Ingest entry: BackBond-side egress receiver (JWT-signed payloads with envelope schema validation), migrated from the prior manifest gateway model.
- Receiver path: signed `post_bind_ingest.receiver` parse + schema + redaction conformance checks.
- Event normalization: `LogEventSchemaV1Event` conforming to Schema v1.0 from already-normalized + already-redacted payloads.
- Outbound monitor gate: `post_bind_ingest.egress` is the single package-level egress boundary. It emits only `monitor-data-contract-v0.1` aggregates, `manifest://` pointers, and length-capped redaction-conformance-gated excerpts; transmit is local-only / no-op by default unless `POST_BIND_INGEST_TRANSMIT=1` and an allowlisted HTTPS endpoint are supplied.
- Legacy Phase 2.0 manifest ingestion is preserved for retirement reference in `tools/post_bind_ingest.phase2_manifest_archive/`.

## Out of scope

- Pull-mode ingestion and any customer-side transport changes — BEAAA-5966.
- Probe execution (P4 / P5 / stubs) — BEAAA-5865 / 5866.
- Sink emitters (claims queue / rescore feed / calibration feed) + observed-vs-predicted appendix — BEAAA-5867.
- Production S3 / KMS / IAM wiring, per-customer credential rotation — Phase 2.0.1 follow-up.
- Active manifest receiver behavior outside archived code.
- `claim_candidate_event` emission — provenance `backbond_detector`; normalizer never emits it.
- Pn taxonomy reconciliation — BEAAA-5864 (does not block this ticket; normalizer never emits `claim_candidate_event`).

## N=5 reproducibility posture

This ticket keeps determinism by construction on the existing scanner score surface:

- `tools/post-bind-ingest/tests/test_no_score_imports.py` enforces no scoring-path imports from `api/`, `scanners/`, or `rubric/`.
- `tools/calibration-deltas/2026-06-16-beaaa5968-receiver-retrofit-vs-master.md` records N=5 `run_l1_n5` with zero spread across baselined fixtures and no breaches.
- Byte-identity of normalized output in this package is covered by receiver fixture replay in `test_receiver.py`.
- BEAAA-7796 adds `test_egress_gate.py`: default no-transmit, endpoint allowlist, single-gate payload construction, and adversarial "nothing raw escapes" rejection for raw PII, secrets, and transcript content.

## Module layout

```
tools/post-bind-ingest/
  src/post_bind_ingest/
    receiver/               receiver envelope parsing + schema + validator
      schema.py            JWT verification + schema validation + conformance checks
      validator.py         signature + clock-skew checks + idempotency
    invariants.py           transport invariants + idempotency-store compatibility
    normalizer.py          input normalizer for already-normalized receiver payloads
    p5_detector.py         P5 transport-level redaction and PII checks
    redaction.py           redaction helper compatibility layer (legacy pattern compatibility)
    manifest_signature.py   compatibility shim for receiver signature behavior
    cli.py                 local payload CLI (`post-bind-ingest` entry point)
    score_invariants.py     BEAAA-5761 defense-in-depth block (importable, schema-pinned)
  tests/                    10 required test modules per CTO brief §3
    fixtures/
      golden_events/        receiver fixture stream
      s3/                   archived manifest-mode regression fixture (from phase 2.0)
      signing_keys/         fixture-only JWT keys
```

## Running tests

```
PYTHONPATH=tools/post-bind-ingest/src pytest tools/post-bind-ingest/tests -q
```

## Archived legacy

The manifest-mode implementation is retained at `tools/post_bind_ingest.phase2_manifest_archive/` for migration traceability and audit comparison.

## Receipts

See `artifacts/beaaa-5863/closeout_receipt.md` for the Phase 2.0 baseline.
