# BackBond consumer claims-review prototype contract

```yaml
owner: Head of Underwriting & AI Risk
artifact_type: contract
status: draft
updated_at: 2026-09-21
source_refs:
  - BEAAA-34392
  - BEAAA-34387 source specification, SHA-256 0a096e6ae962176e88379a6d9e4a8afee838a66434f0b673137885dd6add3c46
claim_or_decision: Define a review-only consumer claims packet and five Stage A fixtures without automatic coverage denial or live payout.
verification: JSON fixtures pass structural validation and assert the required five cases; synthetic identifiers and amounts only.
approvals: []
version: 0.1
```

## Purpose and boundaries

This is an internal implementation contract for CTO and QA. It describes a human-review prototype. It does not establish live protection, approved benefits, capacity, policy wording, a payout service, or a production claim decision.

The packet must let a reviewer reconstruct what the person asked for, what was authorized, what the agent attempted, what an independent source says happened, what was refunded or recovered, and which program terms were applicable. An agent assertion is evidence with agent provenance; it is never the sole final decision.

The prototype accepts a consumer notice even when the detector or provider callback missed the event. Missing evidence produces `evidence_needed` or `reviewing`, never an automatic denial at intake. No LLM may be the sole decision maker for coverage, denial, or disbursement. No fixture authorizes a live payout.

## Packet contract

Each packet is JSON with these top-level fields:

| Field | Required behavior |
| --- | --- |
| `schema_version` | Exact packet schema, currently `claims-review-packet.v0.1`. |
| `fixture_id` | Stable synthetic fixture identifier. |
| `mode` | `prototype_review_only`; no other mode is valid here. |
| `consumer` | Opaque consumer reference only; no raw identity or payment-card data. |
| `original_human_intent` | Immutable captured instruction, normalized interpretation, capture time, and source. Never overwrite after execution. |
| `mandate` | Current mandate version, predecessor, constraints, permitted execution count, and immutable authorization reference. |
| `program` | Versioned program reference, jurisdiction/currency, effective period, per-task limit, and claim deadline. This is a governing configuration reference, not proof that protection is live. |
| `task_protection` | Eligibility/confirmation state, reservation reference, and decision evidence. Keep separate from enrollment and execution. |
| `execution` | Ordered attempts, observed parameters, provider IDs, result status, timestamps, and source provenance. Preserve retries and unknown outcomes. |
| `independent_actual_order_outcome` | Independent provider/merchant result, or an explicit `unknown` object with reason codes. Agent-reported success cannot populate this field as independent evidence. |
| `duplicate_idempotency` | Request key, body digest, event IDs, dedupe result, and proof that a retry did or did not create a second effect. Changed content under an existing key is a conflict. |
| `loss_refund_history` | Claimed, reserved, paid, refunded/recovered, and outstanding amounts as integer minor units. A pending refund is not a recovery until independently settled. |
| `execution_mismatch` | Side-by-side expected versus actual facts, mismatch codes, and candidate-event disposition. An execution mismatch is not a coverage decision. |
| `coverage_decision` | Human-review state, decision, reason, governing term version, evidence references, reviewer identity, and history. Initial values may be `not_evaluated`, `reviewing`, or `evidence_needed`; automation may not set `declined`. |
| `payment_status` | Independent settlement state and settlement identifiers. It must remain `not_authorized` in all Stage A fixtures. |
| `missing_evidence` | Array of machine-readable reason codes, requested items, provenance, and whether each gap blocks reconstruction. Empty means no known gap, not proof of live coverage. |
| `reviewer_explanation` | Plain-language explanation with measured facts, assertions, unknowns, and next action. It must not turn an inference into a fact. |
| `rights_and_access` | Claim-initiation availability, effective-period reference, cancellation/expiry handling, and appeal/correction access. Earlier eligible events remain reportable after later cancellation. |

### Required reason codes

Use stable codes rather than free-text-only gaps: `detector_missed_event`, `provider_callback_omitted`, `provider_event_unverified`, `order_receipt_missing`, `payment_receipt_missing`, `refund_pending`, `mandate_version_missing`, `authorization_proof_missing`, `program_version_missing`, `execution_parameters_incomplete`, `duplicate_proof_missing`, `source_conflict`, and `consumer_assertion_only`.

### State rules

- `execution.status` describes what happened: `not_started`, `in_progress`, `succeeded`, `failed`, or `unknown`.
- `execution_mismatch.status` describes comparison: `none`, `candidate_event`, `unresolved`, or `reconciled`.
- `coverage_decision.status` describes human review: `not_evaluated`, `evidence_needed`, `reviewing`, `approved`, `declined`, `appealed`, or `closed`.
- `payment_status` describes settlement: `not_authorized`, `pending`, `settled`, `reversed`, or `reconciled`.
- A detector event can create or enrich a candidate packet, but cannot set `coverage_decision` to `approved` or `declined`.
- A customer can submit a claim when no detector event exists. The intake record must retain `submitted_by: consumer` and the detector gap.
- Cancellation or expiry prevents new eligibility after the effective end, but does not delete the prior mandate, confirmation, evidence, or claim route for an event inside the prior effective period.
- Every write carries a tenant-scoped idempotency key and request-body digest. Reuse with changed content is a conflict. Provider event IDs are deduplicated separately from client request keys.

## Reviewer explanation contract

The console should render, in this order:

1. Original human intent and the exact authorized mandate version.
2. Requested/approved/executed side-by-side values, with timestamps and source provenance.
3. Independent order/payment outcome and any later refund, reversal, or chargeback.
4. Execution mismatch and candidate-event reason, without implying coverage.
5. Missing evidence and the specific request to the consumer or provider.
6. Governing program version and effective period.
7. Human coverage decision, reason, reviewer, and history.
8. Separate payment status. A review approval is not a payout authorization.

The reviewer must be able to distinguish measured fact, agent assertion, consumer assertion, and derived interpretation. A pending or unknown provider result remains pending or unknown.

## CTO acceptance assertions

- The service validates every required top-level field and rejects a packet whose `mode` is not `prototype_review_only`.
- Original intent is immutable; a material amendment creates a new mandate version and cannot reuse the old authorization silently.
- Independent outcome records are source-tagged and cannot be satisfied by an agent assertion alone.
- Client request idempotency and provider-event deduplication are both tested. A replay does not allocate a second benefit, reserve, reward, or payout; a changed body under one key returns conflict.
- Execution mismatch, coverage decision, and payment status are stored and rendered as separate values.
- Consumer claim intake succeeds with `detector_missed_event` and keeps the claim route available after enrollment cancellation for an event within the prior effective period.
- Claimed, reserved, paid, recovered, and outstanding amounts reconcile in integer minor units; pending refunds do not reduce outstanding loss.
- No route or fixture can transition directly to `paid`, and no automated path can set a final coverage denial.

## QA acceptance assertions

- Validate all five fixtures in `consumer-claims-review-fixtures-v0.1.json`.
- Assert one correct task, one mismatch candidate, one duplicate retry, one missing-evidence packet, and one consumer-initiated reconstruction.
- Assert the exact program version, mandate version, authorization reference, provider IDs, event IDs, body digest, and reason codes remain visible in the packet.
- Replay each duplicate event and request. Assert one authoritative ledger allocation and no second payout.
- Remove the detector event from the reconstruction fixture. Assert intake remains accepted with `detector_missed_event` and coverage remains `not_evaluated` or `reviewing`.
- Cancel the enrollment after the reconstructed task's occurrence time. Assert historical claim access remains available; assert a new post-cancellation task is not eligible.
- Assert a pending refund remains in `loss_refund_history` as `pending`, with `recovered_minor: 0` until settlement.
- Assert the UI labels source provenance, unknowns, mismatch, coverage review, and payment status separately and does not display a live-protection or paid result.

## Open implementation questions

- CTO must bind the packet validator to the actual consumer database schema and durable outbox implementation.
- Claims/Legal must supply the selected program's approved terms before any active protection or payout behavior is enabled.
- QA should add property tests for concurrent limit consumption and restart recovery once the persistence adapter exists.
