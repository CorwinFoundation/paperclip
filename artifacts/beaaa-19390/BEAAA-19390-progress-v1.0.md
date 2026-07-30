---
artifact_type: evaluation_evidence
owner: scanner_engineer_2
persona_audience: internal
canonical_terms_only: true
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-19390"]
scanner_version: "0.5.0"
corpus_version: "fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988"
probe-set-version: "1.4"
rubric_point_id: ["citation_integrity_legal", "hallucination_legal", "policy_refusal"]
n5_calibration_delta: "PASS; residual delta 0 on all eight baselined golden agents"
cto_cosign_ref: null
self_audit_passed: true
---

# BEAAA-19390 saved-response adjudication progress

BLUF: The frozen legacy cohort reconciles exactly to 295 rows: 125 have saved
response text that can enter protocol-v2 review, and 170 have no saved response
and remain uncovered / `not_score_input_eligible`. No Vapi reruns were made and
canonical deterministic results were not changed.

## Classification and protocol

This is a rule-change under
`scanner-implementation-supervision-v1@v1.1`, classification row “Add or change
a judge model,” because replacing the unenforceable legacy seed claim with a
human adjudication policy changes judge/scoring policy. It is not a change to
the N=5 harness, so the normal ±1 gate applies.

Protocol `backbond-saved-llm-judge-adjudication@2.0.0` requires:

- Two independent, named reviewers using each frozen row's rubric and saved
  response text.
- Labels `pass`, `partial`, `fail`, or `abstain`, each with rationale.
- A named adjudicator for every disagreement or abstention.
- Per-row consensus label, numeric score, confidence, method, reviewer evidence,
  and source response hash.
- Raw reviewer agreement and Cohen's kappa across double-reviewed rows.
- Separate publication namespace `adjudicated_saved_llm_judge_v2`; canonical
  deterministic results remain unchanged.

No seed is claimed. The protocol records `seed: null` and temperature 0 only as
the frozen legacy configuration context.

## Evidence

- Review packet:
  `artifacts/beaaa-19390/adjudication-review-packet-v2.json`
- Packet SHA-256:
  `faf15d069d05c6021cda62f232ee93fba39d0716dc3c8c2ee2ea19989564aaba`
- Input-manifest SHA-256:
  `0f82f734a954a99db3928c367b4475e716a27b862db4a36a161d5ab50703c3e3`
- Targeted tests: `3 passed`
- N=5:
  `tools/calibration-deltas/2026-07-30-frozen-0.5.0-vs-beaaa-19390-saved-judge-adjudication.md`
  (`PASS`; residual delta 0 on every reported total and pillar).

## Required continuation

The 125 supportable rows are intentionally unresolved until the CTO co-signs
the protocol and two independent reviewers plus an adjudicator complete the
frozen packet. Applicable Underwriter approval is also required. QA must test
the exact candidate commit and record `APPROVED` on the single canonical QA
child before any promotion. Until then, all 295 legacy rows remain
`not_score_input_eligible`.

## FAL audit

Focus: this work produces reproducible Scanner evidence rather than a new
customer-facing claim. Alignment: it preserves deterministic canonical results
and fails closed without governance approvals. Linkage: it reinforces the
BackBond Scanner's audit-grade evidence discipline.
