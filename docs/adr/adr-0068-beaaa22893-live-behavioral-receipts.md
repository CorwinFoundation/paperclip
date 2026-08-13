---
artifact_type: adr_addendum
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Verified behavioral evidence now reaches the live BackBond Scanner score while retaining its signed forensic chain."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-22893", "BEAAA-22873", "BEAAA-22853"]
proof_points_cited: ["87 focused tests passed", "frozen-roster N=5 seeds 0..4: max total and pillar delta 0"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule-change requires refreshed CTO, Underwriter, and Head of Underwriting & AI Risk co-signs before merge."]
---

# Decision: live behavioral receipt ingestion

BLUF: The live Scorer receives supervisor-built `ScorerReceiptIngestor` capabilities. The scan request cannot supply an ingestor or receipt object. Activation is gated by the exact `development-behavioral-v1` profile, a build-bound `ProductionKeyring`, and scan-scoped immutable evidence resolution.

## Decision

`make_production_scan_runtime` validates the pinned activation configuration before accepting scans. For each scan with trusted receipt channels, it constructs the verification context and wraps the supplied immutable resolver in `ScanScopedEvidenceResolver`. `run_scorer` forwards only those internal capabilities to `ScoreRuntime.compute_envelope`.

The six predicate-to-point mappings and score effects remain unchanged. A verified failure lowers the mapped applicable point to zero. Pass retains the prior raw value. Error and not-applicable do not change the score. No channel means no verified receipt and preserves prior scoring.

For a verified failure, the affected point's existing forensic `artifact_uri` is replaced with the immutable grader-receipt reference. The internal behavioral audit retains the signed receipt ID and the fixture, trace, and grader-receipt references. No API response field was added.

## Failure posture

- Unpinned activation profile, build/keyring mismatch, or empty production keyring fails during runtime construction.
- Peer PID mismatch, invalid signature, unauthorized predicate, cross-scan reference, mutable evidence, or replay conflict fails before score activation.
- Replay state is committed only after full receipt and evidence verification.

## Verification

- Focused regression: 87 passed.
- Frozen roster: six agents, seeds 0 through 4, maximum total delta 0, maximum pillar delta 0. Receipt: `artifacts/beaaa-22893/n5-calibration.json`.
- FAL: Focus passes because the change strengthens continuous Scanner evidence. Alignment passes because it adds no promise or public claim. Linkage passes through the BackBond Scanner and verified evidence proof point.

## Release control

Classification: rule-change, using the supervision-contract rows for a new evidence type and tamper-resistance posture. This candidate must not merge or promote until refreshed CTO, Underwriter, and Head of Underwriting & AI Risk co-signs and exact-candidate approval on canonical QA child BEAAA-22873.

Rollback: revert this live-path integration to `4982604f36c23c3ed57cec68635d8ac2bb495804`, then revert the activation lineage if required.
