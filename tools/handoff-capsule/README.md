# Handoff Capsule v1

Handoff Capsule v1 makes cross-agent work reviewable after the producing session and workspace disappear. It uses Paperclip's existing durable attachment and work-product APIs; it does not require a Paperclip schema migration or a shared Git push.

## Contract

A capsule is a deterministic ZIP containing `handoff-manifest-v1.json`, exact payload bytes, and (for code) a self-contained Git bundle, binary patch, and commit summary. The manifest's canonical JSON determines the 64-character capsule ID. The index's `manifest_sha256` hashes the exact manifest file bytes, including its deterministic trailing newline, and declares `manifest_hash_semantics: raw-file-sha256`. The ZIP is split into 8 MiB parts, each hashed with SHA-256. The published index records every part's Paperclip attachment ID and is uploaded last as the primary attachment-backed `ready_for_review` work product.

Free-text SHAs, local paths, and comments do not satisfy the contract.

## Producer

```bash
python3 handoff_capsule.py build \
  --output-dir /tmp/handoff-producer \
  --producer-issue "$PRODUCER_ISSUE" \
  --qa-issue "$REVIEWER_ISSUE" \
  --repo "$PWD" \
  --candidate HEAD \
  --base origin/master \
  --artifact evidence/n5.json=/tmp/n5.json \
  --artifact evidence/tests.txt=/tmp/tests.txt \
  --verification-command 'pytest -q tests/test_changed_area.py'

python3 handoff_capsule.py verify /tmp/handoff-producer/*.index.json

python3 handoff_capsule.py publish \
  --company-id "$PAPERCLIP_COMPANY_ID" \
  --route \
  /tmp/handoff-producer/*.index.json
```

`publish --route` uploads all parts, creates the primary work product, puts the producer in `in_review`, clears its blockers, puts QA in `todo`, clears QA blockers, and records the exact capsule ID.

## Reviewer

Use the index attachment ID from the work product:

```bash
python3 handoff_capsule.py fetch-verify \
  --company-id "$PAPERCLIP_COMPANY_ID" \
  --index-attachment-id ATTACHMENT_UUID \
  --output-dir /tmp/handoff-review \
  --extract-dir /tmp/handoff-review/materialized
```

For code, clone `/tmp/handoff-review/materialized/git/candidate.bundle`, check out the manifest's `bundle_ref`, and run the declared verification commands. A QA approval must name both the exact capsule ID and candidate SHA.

## Recovery sweep

The sweep repairs one-way lifecycle state without inventing evidence:

```bash
python3 handoff_capsule_sweep.py \
  --company-id "$PAPERCLIP_COMPANY_ID" \
  --pair "$PRODUCER_ISSUE:$REVIEWER_ISSUE" \
  --dry-run
python3 handoff_capsule_sweep.py --company-id "$PAPERCLIP_COMPANY_ID" --discover
```

For pre-capsule discovery, the reviewer issue carries `handoff_capsule_v1: required`, `handoff_role: reviewer`, and `handoff_producer: <producer-issue-identifier>` on separate lines. Producer helpers must not carry the reviewer-role marker. Published capsules are discovered from their work-product summaries. During company migration the verifier accepts attachment-backed `backbond.handoff-capsule*/v1` capsules as legacy input, but new capsules are always published under the `paperclip.handoff-capsule*/v1` namespace. Replacement capsules are ordered by immutable `createdAt`; Paperclip may refresh older work products' `updatedAt`, so it is not a safe recency signal. Candidate identity comes from the verified capsule bytes rather than the summary. The sweep deliberately does not guess from titles or arbitrary issue references.

- Missing/invalid capsule or `CHANGES REQUESTED`: producer actionable, QA blocked by producer.
- Valid capsule pending review: producer `in_review`, QA actionable.
- Exact-capsule `APPROVED`: QA done, producer remains governed, parent owner wakes.

The producer never blocks on its QA issue, so a reciprocal dependency cycle cannot form. The sweep preserves unrelated blockers (for example, a producer-bytes helper) and only repairs the producer/reviewer edge it owns.

## Company-wide rollout and rollback

```bash
python3 rollout_handoff_capsule.py \
  --amendment HANDOFF_CAPSULE_AMENDMENT.md \
  --company-id "$PAPERCLIP_COMPANY_ID" \
  --dry-run

python3 rollout_handoff_capsule.py \
  --amendment HANDOFF_CAPSULE_AMENDMENT.md \
  --company-id "$PAPERCLIP_COMPANY_ID" \
  --apply
```

The rollout is a dry run unless `--apply` is present. It attempts every active Paperclip agent, updates agents whose adapter exposes an instruction bundle, and records unsupported adapters as skipped. It writes the previous `AGENTS.md` content to a timestamped backup directory. Roll back with `--remove --apply`; Paperclip instruction revision history and the local backups remain available. Use `PAPERCLIP_API_KEY` for authenticated instances and `PAPERCLIP_RUN_ID` when the mutation must be attributed to a run.

Run the rollout only after the code PR is reviewed and merged. A dry run is required first; the rollout is intentionally separate from installing this tool.

## Safety boundaries

The tools publish artifacts and repair Paperclip routing only. They do not push Git, merge, deploy, release, change credentials or scope, change scoring/rubrics, spend money, mutate customer data, or replace an independent/protected approval.
