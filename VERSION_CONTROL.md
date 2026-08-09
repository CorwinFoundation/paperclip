# Scanner version-control protocol

GitHub `master` is the only source of truth. Codex worktrees, Paperclip VM
worktrees, and Paperclip artifacts remain drafts until their merge commit is
reachable from `origin/master`.

Read [the source-of-truth and parallel-work runbook](docs/runbooks/version-control-source-of-truth.md)
before starting, handing off, or closing Scanner implementation work.

## Branch lifecycle

- Name every new working branch `agent/beaaa-NNNNN-slug`.
- After merge, create `archive/<branch>` at the merged branch head and delete the branch on the same working day.
- Treat the weekly branch-hygiene workflow as a report-only detector. Its findings do not authorize deletion.
- Preserve history by default. Any non-fast-forward update to `master` is a transplant and must satisfy the Founder-approved policy recorded in BEAAA-21567 and `backbond/architecture/master-history-rewrite-policy-2026-08-09.md` before the push occurs.

Rollback for an archived branch name:

```sh
git push origin refs/tags/archive/<branch>:refs/heads/<branch>
```
