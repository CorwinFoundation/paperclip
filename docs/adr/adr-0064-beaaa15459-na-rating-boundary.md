---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This boundary prevents incomplete measured coverage from becoming an unsupported BackBond Score or protection claim."
contracts_referenced: ["are-scanner-api-v1@v1.0"]
ticket_refs: ["BEAAA-15459", "BEAAA-15456", "BEAAA-15436"]
proof_points_cited: []
version: 0.2
prior_version: "adr-0061-beaaa15459-na-rating-boundary.md@0.1"
self_audit_passed: true
escalations_noted: ["No rubric, corpus, scoring logic, pricing, customer-data, or Ed25519 verification-semantic change; no escalation gate triggered."]
---

# ADR-0064: Suppress ratings until registry-backed measured coverage is complete

## Decision

Customer projections MUST emit `backbond_score: null`, `rating_status: not_available`, and `only 4 of 27 risk areas tested; no rating can be issued.` whenever the registry-backed measured count is lower than the active risk-area count.

## Technical insight

The scanner's synthetic fixture score remains useful for an internal CI gate, but it is not evidence for the 23 risk areas without executable measurement. Separating internal diagnostics from customer projections prevents a technically valid fixture calculation from becoming a false rating or protection claim.

## Contract and invariants

This is an output-boundary correction under `are-scanner-api-v1@v1.0`, not a contract-shape, rubric, corpus, probe-result, or scoring-math change. It strengthens evidence completeness: no numeric BackBond Score is issued without complete measured support. Scanner version remains `0.5.0`; corpus version remains `0.1.1`; no LLM judge changes.

## Implementation boundary

- Derive tested and untested facts from the active registry projection.
- Keep synthetic score, threshold, and score delta only in `internal-diagnostics.json`.
- Emit no numeric customer score in lifecycle events, API projections, tear sheet, certificate, or final report.
- Sign the true partial-scan certificate facts with the existing Ed25519 canonicalization and verification semantics.
- Use the ratified white-background BackBond logo asset with SHA-256 `f32f505a7cb3317003de42e4509a69ea9b758f08a3bb0f41b87a637d8c4a128f`.

## Rejected alternatives

- Labeling `81` as a sample estimate: rejected because cosmetic copy does not stop downstream consumption of the numeric value.
- Removing internal scoring: rejected because the existing synthetic CI regression remains useful and is outside the customer projection.
- Changing Ed25519 verification: rejected because signing the corrected body already makes all partial-scan facts tamper-evident.

## Verification

The focused regression first failed on the leaked numeric customer score, then passed after the boundary was applied. The candidate image must run the focused lifecycle suite in-image; browser/Node tests remain outside the runtime per-scan gate.

## Rollback

Revert the candidate commit and discard its immutable image. Production remains v9 and is not modified by this candidate.

## QA remediation

QA rejected the first candidate because the served final report still rendered the internal fixture score, the image had been built from a dirty worktree, and the N/A projection was hardcoded. Version 0.2 renders a dedicated N/A report for incomplete coverage, derives the projection and reason from registry counts, tests both 4/27 and 27/27 behavior, and requires the replacement image to be built from a clean detached worktree at the exact remediation commit.

## Pre-submission self-audit

1. One outcome: prevent unsupported numeric customer ratings under incomplete measured coverage.
2. Audience/action: QA independently tests the immutable candidate before any promotion request.
3. Supreme Law: evidence completeness is strengthened; reproducibility, calibration, tamper resistance, and discrimination logic are unchanged.
4. No score is shipped in this artifact; internal fixture diagnostics retain scanner/corpus provenance in their source scan artifacts.
5. No contract or scoring change; N=5 calibration delta is not triggered.
6. Rollback is the candidate commit/image revert; production v9 remains intact.
7. No LLM judge was added or changed.
8. Canonical terminology check passed.
9. Carrier Rule check passed; no carrier framing appears.
10. Likely critique pre-empted: the boundary is structural, not cosmetic, and Ed25519 signs the corrected facts.
