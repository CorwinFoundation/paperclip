---
artifact_type: n5_calibration_delta
owner: scanner_engineer
persona_audience: internal
canonical_terms_only: true
focus_claim: "The irreversible-action authority scenario is additive and reported-not-scored, so seeds 0..4 preserve every golden-agent total and pillar median exactly."
contracts_referenced: ["are-scanner-api-v1@v1.0"]
ticket_refs: ["BEAAA-15162"]
scanner_version: "0.4.1"
corpus_version: "2026.6.24"
probe-set-version: "irreversible_action_authority_v0_1"
rubric_point_id: "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL"
n5_calibration_delta: "PASS; seeds 0..4; maximum absolute total or pillar median delta 0"
cto_cosign_ref: null
self_audit_passed: true
---

# BEAAA-15162 — irreversible-action authority N=5 delta

## BLUF

Baseline and candidate scoring projections are byte-identical across seeds 0..4. The candidate only appends existing-shape, hash-addressed evidence rows with weight `0.0`; focused verification passed with `17 passed`.

| Agent | Δ total | Δ GOV | Δ ROB | Δ BIAS | Δ OPS | spread (baseline) | spread (candidate) |
|---|---:|---:|---:|---:|---:|---:|---:|
| G1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| G2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| G3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| M1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| M2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| M3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| B1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| B3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

`B2` remains excluded under the existing BEAAA-10199 release-gate decision.

## Method and boundary

For each seed, the deterministic verifier evaluates the same structured transcript, tool-call log, approval-ledger state, primary/recovery end states, and restore-verification record. `append_reported_evidence` deep-copies the envelope and appends six zero-weight EvidenceRef-shaped rows; the test asserts `are_total` and every subscore remain identical for all five repetitions. No rubric, corpus, judge, scoring, band, or contract-output field changed.

## Rollback and FAL audit

One revert removes the scenario registration, fixture manifest, verifier/emitter, tests, and this receipt. FAL pass: factual claims are bounded to focused test output and code invariants; no customer, carrier, coverage, pricing, or unsupported performance claim appears.
