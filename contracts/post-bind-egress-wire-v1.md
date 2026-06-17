---
artifact_type: other
owner: cto
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "This contract fixes the Phase 2.5 post-bind egress wire shape so BackBond receives only signed, schema-v1.0 typed, already-redacted evidence batches."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1", "log-event-schema-v1@v1.0"]
ticket_refs: ["BEAAA-5975", "BEAAA-5966"]
proof_points_cited: ["ADR-0035 §6.2"]
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted: ["ADR-0035 co-sign complete on BEAAA-5966; this publication is rule-execution per ADR-0035 §8(i)."]
---

# Contract: Post-Bind Egress Wire — v1.0

**Status:** Locked at v1.0 for BEAAA-5975 Phase 2.5 build #3.

**Parties:** CTO (producer) ↔ Scanner Engineer (implementation) ↔ Head of Compliance (security posture). The BackBond Scanner API contract identifier `are-scanner-api-v1@v1.0` remains grandfathered for scanner output shape.

**Source of truth:** This file captures ADR-0035 §6.2 wire shape for the egress receiver. Changes follow the same change-control posture as `log-event-schema-v1.md` and `are-scanner-api-v1.md`.

## Decision

The egress receiver accepts Log Event Schema v1.0 event batches only over HTTPS with mTLS and a signed JWT envelope per batch. The receiver verifies client certificate identity at the transport layer, ed25519 JWT signature at the payload layer, batch replay status, schema version, event count, and batch SHA-256 before forwarding to the existing post-bind normalizer.

## Wire Shape

```http
POST {ingest_endpoint}/v1/events HTTP/1.1
Host: ingest.backbond.ai
Authorization: Bearer {jwt_envelope}
Content-Type: application/json
X-BackBond-Schema-Version: log-event-schema-v1.0
X-BackBond-Batch-Id: {batch_id}
X-BackBond-Batch-Sha256: sha256:{hex}

{"events":[{...schema-v1.0 typed event...}, ...]}
```

## JWT Claims

```json
{
  "iss": "are-collector",
  "sub": "{customer_id}",
  "agent": "{agent_id}",
  "schema_version": "log-event-schema-v1.0",
  "batch_id": "{uuid}",
  "batch_sha256": "sha256:{hex}",
  "event_count": 42,
  "t_emitted": "2026-06-16T18:00:00Z",
  "exp": "<t_emitted + 600s>"
}
```

Algorithm: `ed25519`. Per-bind signing key is generated at bind time. The public key is registered with the egress receiver. Rotation accepts a 24-hour overlap window for clean cutover.

## Receiver Invariants

- TLS version is TLS 1.3 only.
- Client certificate verification is mandatory.
- `schema_version` must equal `log-event-schema-v1.0`.
- `batch_id` is rejected on replay within the receiver replay window.
- `t_emitted` outside the ±300 second receiver skew window is rejected.
- `batch_sha256` is `sha256:` plus SHA-256 over the canonical JSON byte string of the `events` array.
- `event_count` must equal `len(events)`.
- Events are already redacted by the collector. The receiver validates schema and redaction conformance, then passes events to the existing normalizer without in-process redaction.

## Change Control

Changing the wire shape, JWT claim set, cryptographic algorithm, schema version pin, replay posture, or downstream pass-through semantics requires CTO and Head of Compliance co-sign. Any change that mutates BackBond Scanner output shape, scoring math, band cuts, confidence, or evidence requirements also follows `are-scanner-api-v1@v1.0` approval gates.
