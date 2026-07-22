---
artifact_type: adr
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-16747 prevents unverified movement labels from authorizing BackBond Scanner release reliance."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-16747", "BEAAA-16713", "BEAAA-15753"]
proof_points_cited: ["scripts/release_gate_n5.py", "tests/test_release_gate_n5_beaaa10187.py"]
scanner_version: "1.2.0"
corpus_version: "scanner-test-registry-baseline-2026-07-13"
probe-set-version: "wave0-715@frozen"
rubric_point_id: "P1.1-P4.5 (release eligibility only; no scoring change)"
n5_calibration_delta: "pending exact-candidate run"
cto_cosign_ref: "pending fresh CTO co-sign on BEAAA-16747 exact SHA"
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule-change candidate requires fresh CTO co-sign and exact-SHA QA before any integration."]
fal_audit: "pass - one bounded internal claim with direct evidence paths"
---

# ADR-0062: Fail Closed on Release-Movement Approval Provenance

## Decision

The release gate must not subtract an approved movement unless its source carries a distinct, role-complete approval chain. For each movement above one point, the source must identify separate CTO and Underwriter decisions. A movement above three points also requires a distinct Founder decision. A ticket identifier, acknowledgement, reused identity, reused decision reference, or self-reference is not approval evidence.

If any configured movement source fails validation, the gate applies none of the configured movement offsets and returns `invalid_approval_input`, `passed=false`, and `release_reliance_authorized=false`. Existing score, rubric, corpus, judge, architecture-flag, and warranty-recipient behavior is unchanged.

## Classification

Rule-change under `scanner-implementation-supervision-v1@v1.1`, table row "Tamper-resistance posture change." Fresh CTO co-sign is required. This candidate is not merged or promoted.

## Rollback

Delete the BEAAA-16747 candidate branch. Canonical master remains unchanged.
