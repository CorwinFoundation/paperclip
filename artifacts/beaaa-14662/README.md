---
artifact_type: spec
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Approve 513 distinct production-bound bindings against existing BackBond Scanner rubric points, subject only to the required Underwriter corpus-change gate."
contracts_referenced: ["are-scanner-api-v1@v1.0"]
ticket_refs: ["BEAAA-14662", "BEAAA-14659", "BEAAA-14088", "BEAAA-14089"]
proof_points_cited: ["production_bound_case_bindings_v1.jsonl", "production_bound_case_bindings_v1.manifest.json"]
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted: ["Underwriter approval is mandatory because this corpus expansion affects more than 5% of rubric points."]
---

# Production-bound case binding specification

BLUF: CTO approves the 513 bindings in `production_bound_case_bindings_v1.jsonl` as technically executable cases under existing rubric points. Activation remains gated on Underwriter approval of the corpus expansion required by `are-scanner-api-v1@v1.0`.

## Binding decision

- Six existing underwritten failure-mode families are covered: hallucination (86), prompt injection/adversarial input (86), data leak (86), wrong-recipient/misdirected action (85), unauthorized commitment (85), and runaway loop (85).
- Each row has a unique case ID, semantically distinct prompt data, an existing `rubric_point_id`, a meaningful deterministic oracle, version pins, seed, and approved authenticated adapter path.
- Oracles parse structured responses and assert exact values, non-disclosure, recipient integrity, authorization state, or bounded attempts. There is no non-empty-only assertion and no LLM judge.
- Target adapter: `syllable:apichat`, credential reference `phase1-probe.v1`, routes `/api/v1/chats/start_session` and `/api/v1/chats/send_message`. This is the same authenticated production-bound path evidenced by BEAAA-14088/14089; secrets are not embedded.
- Generated responses, mocks, replays, echo targets, and cosmetic duplicate rows are prohibited.

## Contract and calibration posture

- Contract: `are-scanner-api-v1@v1.0`.
- `scanner_version`: `1.2.0`; baseline `corpus_version`: `registry-baseline-f794e63`; candidate `probe_set_version`: `active-probe-registry@0.1.0+beaaa14662.1`; seed `42`.
- Classification: existing-rule execution plus a corpus expansion. No rubric point, scoring weight, scoring math, API output, judge model, or tamper-resistance rule changes.
- Because 513 rows materially expand the corpus and touch six rubric points, Underwriter approval is mandatory before activation. After implementation, run the frozen golden set N=5 and attach total/pillar deltas; any delta outside ±1 requires explicit Underwriter co-sign, and any delta outside ±3 escalates to Founder.

## Acceptance and rollback

Acceptance is satisfied when the manifest verifies exactly 513 unique IDs and prompts, all oracle schemas pass, the authenticated adapter can execute every row, every attempt emits reachable evidence, and N=5 calibration remains within ±1 total and per pillar.

Rollback: remove candidate probe-set `active-probe-registry@0.1.0+beaaa14662.1` from release selection and restore the byte-identical `active-probe-registry@0.1.0` registry. Existing 90 rows and scoring behavior remain unchanged.

## CTO self-audit

1. One goal: unblock 513 truthful production-bound cases.
2. Audience/next action: Underwriter approves the corpus gate; Scanner Engineer implements and executes.
3. Supreme Law: deterministic oracles, complete evidence requirement, N=5 gate, no score-path assumption.
4. No score is reported; all execution pins are specified.
5. No contract shape change; post-implementation N=5 delta is mandatory.
6. Rollback is concrete and byte-stable.
7. No LLM judge is used.
8. Canonical-term check passed.
9. Carrier Rule check passed; carriers are not discussed.
10. Expected critique is pre-empted: activation is not claimed before Underwriter approval and calibration.

## FAL audit

- Focus: exactly 513 executable bindings.
- Alignment: expands evidence on six covered perils without changing pricing semantics.
- Linkage: directly unblocks BEAAA-14659 after the required corpus approval.
