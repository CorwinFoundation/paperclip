---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "The final saved-response adjudication candidate preserves reproducible BackBond Scanner evidence and fails closed on rows the frozen rubric cannot score."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-19422", "BEAAA-19390", "BEAAA-19394", "BEAAA-19395", "BEAAA-19396", "BEAAA-19418", "BEAAA-19419"]
proof_points_cited: ["artifacts/beaaa-19422/final-adjudication-candidate-v1.json", "artifacts/beaaa-19422/n5/n5-calibration.json"]
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule-change co-signs are pinned in the repaired packet; promotion remains blocked until canonical QA approves this exact candidate SHA."]
scanner_version: "0.5.0"
corpus_version: "fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988"
probe-set-version: "1.4"
rubric_point_id: ["citation_integrity_legal", "hallucination_legal", "p1_1_factuality_golden", "p1_3_consistency_golden", "p1_4_refusal_golden", "p1_5_calibration_golden"]
n5_calibration_delta: "PASS; residual delta 0 on total and every pillar for all eight baselined golden agents"
cto_cosign_ref: "/BEAAA/issues/BEAAA-19418#comment-b5f3fee1-147b-4a4b-ae5a-e4071e41f717"
---

# BEAAA-19422 final saved-response adjudication

BLUF: The immutable candidate integrates two independent reviewers and a
distinct adjudicator without rerunning Vapi or changing canonical deterministic
results. It publishes 95 adjudicated score-eligible rows, preserves 30
zero-citation rows as explicitly unresolved and ineligible under the frozen F1
rubric, and retains 170 no-response rows as uncovered: `95 + 30 + 170 = 295`.

## Classification and invariant

This remains a rule-change under the “Add or change a judge model” row of
`scanner-implementation-supervision-v1@v1.1`. It does not change the N=5
harness, so the normal ±1 gate applies. The exact-candidate N=5 result is PASS
with residual delta 0 on total and all four pillars for every baselined golden
agent.

## Final evidence

- Protocol: `backbond-saved-llm-judge-adjudication@2.0.0`
- Publication namespace: `adjudicated_saved_llm_judge_v2`
- Reviewer A: `CTO:88e68019-1cb6-4c4f-83e1-9fd340fb6821`
- Reviewer B:
  `backbond-underwriter-operational:afa65747-4323-419c-9850-bdb77711648f`
- Adjudicator:
  `scanner-engineer-2:bc8396d5-dee6-4ad8-ae3f-1a2280088cb4`
- Raw reviewer agreement: `0.864`
- Cohen's kappa: `0.7277735075582885`
- Targeted tests: `6 passed`
- Candidate artifact SHA-256:
  `383636dbe5a791f841bac4a286aa813f7c69cdfdfbdc44243c4117e6ae3abf70`
- Adjudicator artifact SHA-256:
  `4360f56f2a165c230c0cff1193625ac809efb72ddc6196f2c2e25afd977fc50e`
- Canonical deterministic results unchanged: `true`
- Vapi requests: `0`

## Promotion posture

No unresolved row is promoted. The exact commit produced by this closeout must
be supplied to canonical QA child BEAAA-19396, which must record
evidence-backed `APPROVED` for that SHA before any shared or production
promotion.

## FAL audit

Focus: reproducible Scanner evidence supports BackBond's trust-layer claim.
Alignment: unresolved evidence fails closed and no protection claim changes.
Linkage: the candidate retains the pinned Scanner contract, corpus, probe set,
governance refs, and exact-candidate N=5 evidence.
