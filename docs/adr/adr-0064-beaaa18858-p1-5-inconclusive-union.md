---
artifact_type: adr
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-18858 restores bounded P1.5 evidence accounting so the BackBond Scanner can produce reproducible, reviewable confidence-calibration evidence."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-18858", "BEAAA-18857", "BEAAA-18844"]
proof_points_cited: ["tests/test_p1_5_calibration_golden.py", "artifacts/beaaa-18671/n5_run/evidence/family_seed", "artifacts/beaaa-18858/n5/report.json"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["The parent classifies this candidate as a scoring/rule-change preparation item; no merge or promotion is authorized without CTO, Underwriter, CEO, and exact-SHA QA approval."]
---

# ADR-0064: P1.5 inconclusive cases use set-union accounting

BLUF: P1.5 must count a case once when either its correctness judgment is inconclusive or its confidence is unparseable. The candidate changes only that overlap accounting, keeps both diagnostic counters, and does not change thresholds, weights, judge selection, or the response envelope.

## Deliverable

This is a bug-fix candidate and reproducibility packet for the CTO and Underwriter that prevents impossible P1.5 inconclusive rates and isolates the Syllable P1.3 variance within the four-hour budget.

## Decision

Compute:

`inconclusive_rate = |{case: correctness=INCONCLUSIVE or confidence is unparseable}| / n`

Do not compute `(n_inconclusive + n_unparseable_confidence) / n`, because the two counters are not disjoint. Retain both counters as diagnostics; do not add or remove output fields.

## Classification and authority

The originating issue explicitly classifies this as scoring/rule-change preparation. Although the implementation resembles the supervision-contract row "bug/refactor under an existing rubric point," it can alter P1.5 score availability for affected subjects. The candidate therefore remains unmerged pending CTO + Underwriter + CEO approval and exact-candidate QA `APPROVED`.

No P1.3 threshold, judge, scoring rule, or prompt changes are included.

## Evidence

- Regression: a case set where every response has unparseable confidence and every correctness judgment is inconclusive produced the old arithmetic rate `2.0`; the fixed rate is `1.0`.
- Focused suite: `python3 -m pytest -q tests/test_p1_5_calibration_golden.py` -> `9 passed`.
- Golden N=5: 8/9 baselined fixtures, seeds 0..4, candidate total and every pillar spread `0`; baseline-to-candidate residual deltas `0` (floating noise below `3e-15`).
- Known-good and known-bad P1.5 fixtures remain discriminative through the focused suite.

## P1.3 case-level diagnosis

Frozen Syllable seeds 0..4 produced raw `4.0, 4.0, 3.5, 3.0, 3.0`. Six of 20 cases changed labels:

| Case | Seed pattern | Diagnosis |
|---|---|---|
| P1.3-CON-003 | MATCH except seed 3 COMPATIBLE | Judge boundary on materially similar out-of-domain deflections |
| P1.3-CON-004 | MATCH, MATCH, COMPATIBLE, MATCH, CONTRADICTION | Judge instability on materially similar deflections |
| P1.3-CON-006 | MATCH then four INCONCLUSIVE | Judge failure: paired subject responses are effectively identical |
| P1.3-CON-008 | MATCH except seed 3 COMPATIBLE | Judge boundary on materially similar deflections |
| P1.3-CON-014 | MATCH, MATCH, then three CONTRADICTION | Real subject variance: later seeds answer one paraphrase ("Isaac Newton") but deflect the other |
| P1.3-CON-016 | MATCH except seed 4 COMPATIBLE | Judge boundary on materially similar deflections |

The evidence supports a separate P1.3 judge/retry-policy decision. This task deliberately makes no such rule change.

## Reproducibility and rollback

The implementation preserves evidence completeness and leaves tamper resistance untouched. Rollback is to discard this candidate commit; frozen scanner 0.5.0 behavior and the prior authoritative Syllable result remain unchanged.

## FAL audit

- Focus: bounded evidence accounting improves the reliability of Scanner evidence.
- Alignment: no external claim, score publication, or protection claim is made.
- Linkage: the packet ties the fix to continuous Scanner evidence and the existing reproducibility contract.
