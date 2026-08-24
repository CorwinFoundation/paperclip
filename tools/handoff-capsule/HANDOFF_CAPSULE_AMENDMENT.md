<!-- PAPERCLIP-HANDOFF-CAPSULE-V1:BEGIN -->
## Handoff Capsule v1 — durable cross-agent work transfer

This policy applies to every material producer → reviewer, engineer → QA, data → analyst, and evidence → approver handoff. A comment, branch name, local path, screenshot, or commit SHA is a pointer, not a deliverable. The recipient must be able to reproduce the review from Paperclip after the producer workspace and session disappear.

### Required capsule

Before claiming `ready_for_review`, the producer MUST publish one attachment-backed Paperclip work product titled `Handoff Capsule v1 index <capsule-id-prefix>`. Its index MUST identify:

- producer and reviewer issue identifiers;
- immutable capsule ID and SHA-256 for every payload part;
- exact candidate/base identity when Git is involved;
- all source artifacts, test output, N=5 receipts, provenance/calibration evidence, and verification commands required by the issue;
- prerequisite capsule IDs when the result depends on bytes not present in the capsule.

Every new or reused reviewer issue MUST contain these exact machine-readable lines so the recovery sweep can manage the pair before a capsule exists:

```text
handoff_capsule_v1: required
handoff_role: reviewer
handoff_producer: <producer-issue-identifier>
```

Code handoffs MUST include a self-contained Git bundle and binary patch. Non-code handoffs MUST include the exact documents, datasets, evidence, or rendered artifacts under review. Split payloads below the Paperclip attachment limit; upload all parts to the producer issue; upload the published index last; make that index the primary `ready_for_review` work product.

Canonical publisher/verifier: `tools/handoff-capsule/handoff_capsule.py`. If that path is unavailable in a managed workspace, use the same manifest/index contract and Paperclip attachment + work-product APIs; do not fall back to free text.

### Lifecycle (one direction, never a cycle)

1. While bytes are missing or QA requested changes: producer is `todo`/`in_progress`; QA is `blocked` by the producer.
2. When a valid capsule exists: producer is `in_review` with no QA blocker; QA is `todo`/`in_progress` with no producer blocker.
3. `CHANGES REQUESTED` or an invalid capsule returns the producer to `todo` and QA to `blocked` by the producer.
4. `APPROVED` is valid only when the independent reviewer names the exact 64-character capsule ID (and exact candidate SHA when applicable). Then wake the documented parent owner. Do not auto-merge, deploy, release, score, change credentials/scope, or satisfy a protected approval.

The producer and QA issues MUST NOT block each other. A producer blocked by its own reviewer is a routing defect, not governance. The recovery sweep removes only the reciprocal producer/reviewer edge; it preserves unrelated dependency children and prerequisites.

### Reviewer rule

Do not test a free-text-only SHA or an unshared workspace. Fetch the published index attachment, verify all part hashes, reassemble the capsule, verify the internal manifest and Git bundle (if present), then run the declared commands on clean materialized bytes. If any byte, prerequisite, receipt, or identity is missing, record `CHANGES REQUESTED` and route the producer back to work; do not leave both sides blocked.

### Bounded-run checkpoint

By tool call 10 (or before a run budget expires), a producer must either publish the QA-ready capsule or publish a resumable checkpoint capsule containing current bytes, exact state, remaining commands, and blockers. Do not open or wake a QA leaf until the QA-ready capsule work product exists.

The recurring operator runs `handoff_capsule_sweep.py --discover`. Discovery is fail closed: it manages only reviewer issues carrying all three machine-readable lines above or pairs named by an existing attachment-backed capsule. Producer helpers and evidence-generation children MUST NOT carry `handoff_role: reviewer`. The sweep never infers a workflow from titles, arbitrary issue links, or the word `QA`.

### Independence and protected decisions

The producer cannot serve as its own independent reviewer. Handoff Capsule v1 makes bytes durable; it does not waive QA, CTO, Founder, security, legal, production, deployment, release, credential, spend, scoring/rubric, destructive-action, or customer-data approval requirements.
<!-- PAPERCLIP-HANDOFF-CAPSULE-V1:END -->
