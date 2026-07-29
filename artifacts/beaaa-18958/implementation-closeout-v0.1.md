---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Deterministic Passive Monitor latency evidence strengthens the continuous verification required to support BackBond's embedded trust layer."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-18958", "BEAAA-18947"]
proof_points_cited: []
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["No escalation: reported-not-scored additive rule execution; no score, contract-shape, judge, rubric, or tamper-resistance change."]
---

# BEAAA-18958 implementation closeout

BLUF: Passive Monitor breach detection latency is now reproducibly reported
from four immutable timestamps, evaluated against a fixture-only SLA, and linked
to source event IDs and content hashes without changing continuity freshness or
any BackBond Score.

## Classification and pins

- Deliverable: This is evidence-pipeline glue for the CTO that produces an
  auditable breach-detection latency verdict within the five-hour budget.
- Classification: rule-execution, using the supervision contract row for a
  smoke test of an existing operation and the additive implementation precedent.
- Scanner version: `0.4.1`
- Corpus version: `2026.6.24`
- Probe-set version: `retell-consolidated-probe-set@2026-06-24`
- Rubric point ID: `N/A - reported-not-scored Passive Monitor evidence`
- Contract anchor: `contracts/are-scanner-api-v1.md@v1.0`
- CTO co-sign: not required
- Upstream eval version: not applicable

## Implementation

`determination/detection_latency.py` derives four integer-millisecond values:
event-to-collector, collector-to-determination, determination-to-routing, and
event-to-routing. It returns `on_time`, `late`, `indeterminate`, or
`invalid_clock_skew`, hashes the complete canonical preimage into `record_id`,
and retains canonicalized source event IDs with their content hashes.

The SLA remains in
`determination/fixtures/detection_latency/breach_latency_v1.yaml`, explicitly
marked `fixture_only`, `reported_not_scored`, and `freshness_policy: excluded`.
The additive emitter rollback switch is
`BACKBOND_DETECTION_LATENCY_EMIT`, default off.

## Verification

- Focused regression: `66 passed in 2.51s`
- Coverage: on-time, late, missing timestamp, clock skew, deterministic replay,
  and conflicting-hash replay rejection
- N=5: five identical records
  - record ID:
    `7ddca7613e30f73c20e8c9434c48cee86b6edeb6b746010d5ef8917c381b1369`
  - serialized record SHA-256:
    `5146269a172b8e399354f88e1a991e0d0fca964912df63c978336495133ce69a`
  - verdict each run: `on_time`
  - BackBond Score delta each run: `0`
  - maximum golden-set delta: `0`, within the required ±1 band
- Syntax check: `python3 -m py_compile` passed

## FAL audit

- Focus: pass. The evidence makes continuous verification measurable.
- Alignment: pass. The record is operational evidence, not a new product or
  coverage promise.
- Linkage: pass. It reinforces the two-sensor underwriting loop and Passive
  Monitor production evidence.

## Rollback

Leave `BACKBOND_DETECTION_LATENCY_EMIT` off and remove the fixture rule. Existing
event ingestion, determinations, continuity freshness, and scoring remain
unchanged.
