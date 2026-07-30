---
artifact_type: closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This closeout gives internal decision-makers exact Vapi evidence denominators without converting partial coverage into an unsupported BackBond Score."
contracts_referenced: ["are-scanner-api-v1.md@v1.3.0", "scanner-implementation-supervision-v1.md@v1.1"]
ticket_refs: ["BEAAA-19518", "BEAAA-19388"]
proof_points_cited: []
version: 1.1
prior_version: "artifacts/beaaa-19388/BEAAA-19388-vapi-2000-coverage-closeout-v1.0.md"
self_audit_passed: true
escalations_noted: []
---

# Vapi closeout v1.1

## Decision

BEAAA-19388 v1.0 is superseded as the current accounting view, but retained as history. The v1.1 JSON ledger reconciles 2,000 frozen rows and preserves four separate denominators. It makes no overall Vapi BackBond Score or band claim.

## Exact behavior denominator

The deterministic baseline contains 255 valid judged cases. Final adjudication adds exactly 95 net-new valid cases: `255 + 95 = 350`. The 350 valid behavior-judged cases are 284 PASS, 56 FAIL, and 10 PARTIAL. The displayed `81.1%` is `284 / 350` only.

## Terminal accounting

- 642 deterministic INVALID rows reconcile exactly under `unsupported_family`; each row records evidence, missing verifier capability, attempted remedy, owner, and next fix.
- 170 uncovered judge rows have row-level terminal records and evidence pointers.
- 30 adjudication rows remain unresolved and fail closed.
- 596 synthetic capability cases remain a separate, non-Vapi-target denominator.

The machine-readable family scorecard records attempted and fully judged denominators, row IDs, status, result counts, and evidence pointers for each family.

## Evidence

- Ledger: `artifacts/beaaa-19518/vapi-closeout-terminal-ledger-v1.1.json`
- Ledger SHA-256: `48e3d2b6196df9aebd8a26064b06d91491a15d913f17001988f43ef5ccac9a6d`
- Scanner version: `0.5.0`
- Corpus version: `fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988`
- Probe-set version: `1.4`
- Rubric points affected: none
- N=5 calibration delta: `0.0000` because this is a derived-evidence build with no scoring or harness change
- Contract: `contracts/are-scanner-api-v1.md@v1.3.0`

## Rollback

Discard the v1.1 derived outputs and retain v1.0 plus the frozen source evidence. Make no score or band claim.
