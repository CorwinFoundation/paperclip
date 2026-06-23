---
artifact_type: other
owner: cto
persona_audience: prospect_ai_vendor
phase: 1
canonical_terms_only: true
focus_claim: "This install note keeps the BackBond post-bind Monitor local to the customer environment until outbound egress is explicitly approved."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-7819", "BEAAA-7799", "BEAAA-7796", "BEAAA-5968"]
proof_points_cited: []
version: 0.2
prior_version: "tools/post-bind-ingest/README.md@8fc91d3"
self_audit_passed: true
escalations_noted: []
brand_backbone_version: "v1.4"
brand_backbone_sha256: "241d10820ae194119934a2a58e6d3a6584eac722edc33e5c23d3c0f2389d251b"
brand_backbone_verified: true
---

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
- Local Monitor container: `Dockerfile` + `docker-compose.local.yml` validate JWT-signed receiver payloads in a no-network container and write normalized JSONL to a customer-mounted output directory.
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

## Local Monitor container

BLUF: this container validates a Log Event Schema v1.0 receiver payload and runs passive post-bind Monitor detectors inside the customer environment. The default runtime has no network and writes normalized JSONL to a local mounted output directory.

Contract anchor: `are-scanner-api-v1@v1.0`. This package does not change BackBond Scanner scoring math, rubric weights, judge models, or output shape.

### Local install

```bash
cd tools/post-bind-ingest
mkdir -p customer-input customer-output
cp /path/to/receiver_payload.json customer-input/receiver_payload.json
export BACKBOND_MONITOR_JWT_PUBLIC_KEY='replace-with-customer-public-key'
docker compose -f docker-compose.local.yml up --build --abort-on-container-exit
```

Output lands at:

```text
tools/post-bind-ingest/customer-output/normalized-events.jsonl
```

### Local-only defaults

- `docker-compose.local.yml` sets `network_mode: "none"`.
- The image runs as the unprivileged `monitor` user.
- The container filesystem is read-only except mounted `/output`.
- `BACKBOND_EGRESS_ENABLED` defaults to `false`.
- `--egress-url` is rejected unless `--enable-egress` is also present.
- This build still does not implement outbound transport. Egress remains gated on BEAAA-7796 and Q2 ratification.

### Dry run

```bash
docker run --rm \
  --network none \
  -v "$PWD/customer-input:/input:ro" \
  backbond/post-bind-monitor:0.1.0-local \
  /input/receiver_payload.json \
  --jwt-public-key "$BACKBOND_MONITOR_JWT_PUBLIC_KEY" \
  --dry-run
```

Expected output:

```text
events_normalized=<count>
```

### Operational metadata

- `scanner_version`: unchanged, Scanner frozen
- `corpus_version`: unchanged, Scanner frozen
- `probe-set-version`: `post-bind-detectors-v0.1`
- `rubric_point_id`: `OPS.DATA_HANDLING.LOCAL_INGEST`, `ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL`, `ROBUSTNESS.TOOL_MISUSE.RUNAWAY_LOOP`
- `N=5 calibration delta`: dry delta `0`; no Scanner code path imported or modified
- `cto_cosign_ref`: not required; rule execution under `scanner-implementation-supervision-v1@v1.1`

## Archived legacy

The manifest-mode implementation is retained at `tools/post_bind_ingest.phase2_manifest_archive/` for migration traceability and audit comparison.

## Receipts

See `artifacts/beaaa-5863/closeout_receipt.md` for the Phase 2.0 baseline.
