# Version-control source of truth and parallel-work protocol

Last reconciled: 2026-07-13 UTC.

This runbook defines where Scanner code is authoritative and how Codex and
Paperclip agents work in parallel without overwriting each other. The short
rule is:

> A change is part of the Scanner only after its merge commit is reachable
> from `origin/master` in `CorwinFoundation/are-scanner`.

Paperclip ticket status, uploaded artifacts, a local commit, and a pushed
feature branch are useful evidence, but none of them replaces a merge to
GitHub `master`.

## The three environments

| Environment | Purpose | Authority |
| --- | --- | --- |
| GitHub `CorwinFoundation/are-scanner` | Review, CI, merge history, releases | **Canonical.** `master` is the only source of truth. |
| Windows/Codex worktrees | Codex implementation and review | Local drafts until pushed, reviewed, and merged. |
| Paperclip VM worktrees | Company-agent implementation | VM drafts until pushed, reviewed, and merged. |

Paperclip issues and attachments are the coordination record. They are not a
source-code repository and must always include GitHub branch, commit, PR, and
merged-SHA references when code is involved.

## Reconciliation snapshot

This table is a dated recovery snapshot, not a permanent test-count claim.

| Lane | Location and state | Disposition |
| --- | --- | --- |
| Canonical production baseline | `origin/master@10400821d03d718e2a00f6c33eef775d57b2718f`; PRs #66 and #67 merged; authenticated 90-case execution proven | Starting point for every new worktree. |
| Company Appendix A reconciliation | `beaaa-13802-clean-reconcile@35f7f43600c053364058a7cf01fab14cd24c1998`; one commit directly on `1040082` | **Canonical company lane for BEAAA-13802.** Open/merge its PR before downstream registry work. |
| Duplicate company reconciliation branch | `beaaa-13802-master-reconcile@3ea64b1fe7d39983df97793c5d3cc4a28983b3f5`; identical tree and parent to the canonical company lane | Frozen. Do not add commits. Remove after the canonical PR merges. |
| Codex text-case activation draft | Windows branch `feat/activate-text-workers` at `1040082` with five local modified/untracked files | Paused and not authoritative. Reconcile only after BEAAA-13802 merges. |
| Paperclip shared checkout | VM branch `beaaa-12093-master-candidate@34687b5`; at audit time its cached `origin/master` was `ad1b172`, with 121 modified/untracked paths and no `tools/registered_case_executor.py` | Quarantined as a recovery source. Do not commit, merge, reset, or push this mixed checkout. |

The VM shared checkout contains work from unrelated tickets. Preserve it until
each ticket-owned change is either ported to a clean worktree or explicitly
discarded by its owner.

## Required workflow

### 1. Start from GitHub, never from another agent's checkout

```bash
git fetch origin --prune
git worktree add -b beaaa-<ticket>-<description> ../wt-beaaa-<ticket> origin/master
cd ../wt-beaaa-<ticket>
python tools/version_control_preflight.py
```

Codex-owned coordination branches use `agent/<description>`. Company code
branches use the existing `beaaa-<ticket>-<description>` convention. One
ticket has one active integration branch unless the ticket explicitly records
a replacement.

### 2. Declare ownership before editing

The Paperclip ticket comment must state:

- owner and environment (`Codex/Windows` or `Company/Paperclip VM`);
- branch and base SHA;
- files or subsystem owned;
- expected handoff (commit, draft PR, or artifact only); and
- any branch that is frozen or superseded.

No agent edits a file owned by another active lane without a ticket handoff.

### 3. Keep local, pushed, merged, and deployed states distinct

Use these terms literally:

- **local**: changes exist only in a worktree;
- **committed**: a local commit exists;
- **pushed**: GitHub has a feature branch;
- **in PR**: a GitHub pull request targets `master`;
- **merged**: the change is reachable from `origin/master`;
- **deployed**: a deployment record identifies the merged SHA.

Never close a code ticket as merged or deployed using only a workspace path,
test output, artifact upload, or feature-branch SHA.

### 4. Reconcile before publishing

Before a push or PR update:

```bash
git fetch origin --prune
python tools/version_control_preflight.py
git diff --check
```

If `master` advanced, update the branch in its own worktree. Never force-push
someone else's branch. Never merge the Paperclip shared checkout wholesale.
Port ticket-owned commits or files into a clean master-based worktree and
review the resulting diff.

### 5. Merge through one queue

The integration order for the current execution-layer work is:

1. BEAAA-13802 Appendix A reconciliation;
2. canonical registry and five dispatcher-gap repairs;
3. company loop-stability, hallusquatting, and Jade Puffer ports;
4. Codex text-compatible case activation;
5. authenticated full-suite execution and deployment.

Each downstream branch refreshes from the newly merged `origin/master` before
it is opened or updated. An advertised executable-test count changes only
after the corresponding target execution and signed reconciliation manifest
are merged.

## Pull-request and ticket close gate

A code ticket may close only when its comment records all applicable fields:

```text
branch:
commit_sha:
pull_request:
merged_sha_on_canon_master:
base_master_sha:
files_or_subsystem_owned:
verification:
execution_manifest_or_not_applicable:
deployment_sha_or_not_deployed:
superseded_branches:
```

Verify master reachability rather than trusting the field:

```bash
git fetch origin --prune
git merge-base --is-ancestor <merged_sha_on_canon_master> origin/master
```

A zero exit status proves reachability. Any other result means the code is not
on canonical master.

## Current ownership

| Scope | Owner | Active lane | Do not touch |
| --- | --- | --- | --- |
| Appendix A row-level reconciliation | Company/CTO via BEAAA-13802 | `beaaa-13802-clean-reconcile` | Duplicate `beaaa-13802-master-reconcile` |
| 113 text-compatible case draft | Codex | local `feat/activate-text-workers` (paused) | Do not push or rebase until BEAAA-13802 merges |
| Version-control protocol | Codex | `agent/version-control-source-of-truth` | No production code |
| Mixed historic VM changes | Individual ticket owners | quarantined shared checkout | No bulk commit, reset, merge, or push |

When this table changes, update it in the same PR that transfers ownership or
post the handoff on the relevant Paperclip ticket before work continues.
