---
artifact_type: other
owner: underwriter
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-8133 approves the P2.2 candidate corpus only as a signed labeled-outcome substrate for later live calibration, not as a measured BackBond Scanner score path."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-8133", "BEAAA-8129", "BEAAA-8123"]
proof_points_cited:
  - "artifacts/beaaa-8129/p2_2_equal_opportunity_labeled_outcome_corpus_v0.1.jsonl"
  - "artifacts/beaaa-8129/manifest.json"
  - "artifacts/beaaa-8129/measurement_spec.md"
  - "corpus/bias/equal_opportunity/equal_opportunity.yaml"
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted:
  - "No Head of Underwriting escalation on this heartbeat: the decision is corpus-stage rule execution only; score-bearing activation remains separately gated on live wiring plus N=5 calibration."
---

# Underwriter Sign-Off — BEAAA-8133 / BEAAA-8129 P2.2 Candidate Corpus

## BLUF

Approved as a candidate labeled-outcome substrate for `P2.2` equal-opportunity gap. Not approved as a measured or pricing-valid BackBond Scanner output until the live probe path is wired, the active probe mapping is updated to the signed packet shape, and N=5 golden calibration passes under `are-scanner-api-v1@v1.3.0`.

## Decision

This heartbeat is rule execution, not a scoring-rule change. I approve the corpus packet for candidate-stage use because:

1. The row schema is consistent across all 18 rows and preserves a deterministic label space (`positive_outcome` / `negative_outcome`) with no LLM judge dependency.
2. The matched comparison sets keep task-relevant factors fixed while varying protected attributes, which is the minimum valid substrate for equal-opportunity review.
3. The packet is synthetic, hash-pinned, and explicitly non-score-bearing pending later live calibration.

## Scope Of Approval

Approved now:

- `artifacts/beaaa-8129/p2_2_equal_opportunity_labeled_outcome_corpus_v0.1.jsonl` as the signed candidate labeled-outcome corpus
- `artifacts/beaaa-8129/measurement_spec.md` as the candidate-stage measurement method
- `artifacts/beaaa-8129/manifest.json` as the packet metadata record

Not approved now:

- any measured `P2.2` score emission
- any pricing use of `P2.2`
- any claim that the live scanner path is already wired

## Required Next Gates Before Measured Use

1. Update the live probe mapping in `corpus/bias/equal_opportunity/equal_opportunity.yaml` so it points to the signed packet shape rather than the older `corpus.yaml` / CSV references.
2. Wire the live-fire runner under the existing BackBond Scanner output shape with deterministic normalization to the allowed label pair.
3. Run N=5 golden calibration with seeds `0..4` and attach the per-agent total and pillar deltas.
4. Halt the merge and reclassify if any golden total or pillar delta exceeds `±1`.

## Operational Notes

- `scanner_version`: `0.4.1` target, unchanged at corpus-sign-off stage
- `corpus_version`: `p2_2_equal_opportunity_labeled_outcome_v0.1_candidate`
- `probe-set-version`: `bbq-holisticbias-phase1-v0.1.0 + p2_2_equal_opportunity_candidate_v0.1`
- `rubric_point_id`: `P2.2`
- `N=5 calibration delta`: not applicable at corpus-sign-off stage; required before score-bearing merge
- Deprecated score-name check: pass; no deprecated score or company terms propagated in the signed packet

## Self-Audit

1. One thing this artifact does: signs or rejects the P2.2 candidate corpus packet.
2. Rule execution vs rule change: rule execution at corpus-candidate stage; score-bearing activation remains separately gated.
3. N=5 attached: not applicable yet and explicitly held open.
4. Head of Underwriting co-sign reference: not required for this corpus-stage operational sign-off; required if later work changes scoring behavior.
5. Scanner / corpus / probe-set pins cited: yes.
6. Evidence package updated: yes, via this sign-off artifact plus manifest update.
7. Deprecated terms checked: yes.
8. Glossary check run: yes.
9. Contract cited: yes, `are-scanner-api-v1@v1.3.0`.
10. Likely review critique pre-empted: yes; the stale live probe mapping is called out as a hard gate before measured use.
