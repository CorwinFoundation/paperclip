---
artifact_type: deployment_observation_checklist
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "Pre-authorized observation and evidence checklist for the first two production releases through the ADR-0057 descendant scanner-edge pipeline; no production access or promotion is granted by this document."
directive_ref: "BEAAA-15604 revision aecee8cb-a23c-4ed2-8ff3-585628140f93"
decision_source: "BEAAA-15606"
contracts_referenced: ["ADR-0057", "BEAAA-15400", "BEAAA-15373"]
ticket_refs: ["BEAAA-15604", "BEAAA-15606", "BEAAA-15607", "BEAAA-15400", "BEAAA-15373"]
classification: "rule-execution; deployment observation only; no scoring, rubric, corpus, band, API, or contract-surface change"
scanner_version: "0.5.0"
corpus_version: "n/a — operational checklist; no corpus read or emitted"
probe-set-version: "n/a — operational checklist; no probe executed"
rubric_point_id: "none — deployment observation only"
n5_calibration_delta: "n/a by construction — documentation-only change outside scanner runtime and score surfaces"
underwriter_gate: not_required
underwriter_gate_reason: "No score or pricing surface changes."
contract_output_shape_unchanged: true
non_score_moving: true
qa_gate_required: true
founder_production_signoff_required: true
self_audit_passed: true
---

# First two production deploys — observation and evidence checklist

## Gate before either merge or promotion

- [ ] QA Engineer records the exact word `APPROVED` on the originating issue for
  the exact candidate commit SHA.
- [ ] Founder signs off on public/production promotion under BEAAA-15373.
- [ ] Candidate commit is the current `origin/master` SHA after its ordered merge.
- [ ] Required `python pytest` and `golden scanner regression` checks are green.
- [ ] Release sequence is positive, monotonic, and unused.
- [ ] Candidate metadata names `srv1749248` and `production`.
- [ ] No agent is given production-box, key, or signing-material access.

Passing tests are evidence only. They do not replace QA approval or Founder
production sign-off.

## Record for deploy 1 — rollback exercise

Before activation, copy immutable values into the table. Do not infer them after
the fact.

| Field | Recorded value |
| --- | --- |
| Merge PR | |
| Commit SHA | |
| Workflow run URL | |
| Image digest | |
| Signature bundle SHA-256 | |
| SBOM SHA-256 | |
| Candidate id | |
| Authorization id | |
| Release sequence | |
| QA approval comment | |
| Founder sign-off reference | |

Observe and record:

- [ ] Candidate signature identity and OIDC issuer verify.
- [ ] Candidate, authorization, host, environment, SHA, digest, and sequence bind.
- [ ] Deploy lease is valid and used once.
- [ ] Pre-activation image/version is recorded as the rollback target.
- [ ] Activation reaches terminal state; no `activation_pending` evidence remains.
- [ ] SLA endpoint passes.
- [ ] Unauthenticated and forged-key requests are rejected.
- [ ] Dedicated non-`vapi` smoke scan passes.
- [ ] Scan-log pins and customer-envelope checks pass.
- [ ] Terminal receipt matches the immutable candidate and authorization.

Rollback exercise result:

- Trigger method (must be a safe, pre-authorized acceptance failure):
- Expected result: previous image restored, health rechecked, terminal receipt
  records failed or rolled-back outcome.
- Actual result:
- Previous image id/version after restoration:
- Receipt id and evidence references:
- Operator notes:

Do not introduce a failure into a live customer request path. If no safe failure
injection has been explicitly authorized, record the rollback exercise as
blocked and name the Founder as unblock owner; do not improvise.

## Record for deploy 2 — clean forward activation

| Field | Recorded value |
| --- | --- |
| Merge PR | |
| Commit SHA | |
| Workflow run URL | |
| Image digest | |
| Signature bundle SHA-256 | |
| SBOM SHA-256 | |
| Candidate id | |
| Authorization id | |
| Release sequence | |
| QA approval comment | |
| Founder sign-off reference | |

Observe and record:

- [ ] Candidate signature identity and OIDC issuer verify.
- [ ] Candidate, authorization, host, environment, SHA, digest, and sequence bind.
- [ ] Deploy lease is valid and used once.
- [ ] Activation reaches terminal success; no `activation_pending` evidence remains.
- [ ] SLA, auth rejection, smoke scan, scan-log pins, and customer envelope pass.
- [ ] `current_version` equals the second merge SHA.
- [ ] Terminal receipt matches the immutable candidate and authorization.
- [ ] Production is now running an image built from current `origin/master`.

## Closeout evidence

Attach both pipeline-posted terminal deploy records to BEAAA-15607. For each,
include the exact SHA, image digest, workflow run, approval references, sequence,
acceptance results, rollback outcome (deploy 1), and terminal receipt. A ticket
count or a green workflow without the matching receipt is not completion.
