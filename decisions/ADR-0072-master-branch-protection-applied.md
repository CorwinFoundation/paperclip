---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This ADR records that master branch protection was applied on 2026-08-22 with enforce_admins on and a required approving review from a second identity, supersedes ADR-0037's blocker status, and states what changes for every operational seat."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v2.0"]
ticket_refs: ["BEAAA-24442", "BEAAA-24491", "BEAAA-8219"]
supersedes: ["ADR-0037 (Blockers section only)"]
version: 1.0
prior_version: null
self_audit_passed: true
---

# ADR-0072: master branch protection applied, and what it changes for every seat

## Decision

As of 2026-08-22, `refs/heads/master` on `CorwinFoundation/are-scanner` is
protected with **no administrator exception**. A pull request, one approving
review from an identity other than the pusher, and three green required checks
are now mandatory for every landing, including landings by the account the agent
fleet pushes with.

This is infrastructure / repo-governance. No rubric point, pillar weight, scoring
math, judge model or tamper-resistance posture changes, so no Underwriter
co-sign or Founder rubric gate is triggered.

## Settings in force

Applied to the existing classic branch-protection rule on `master`, not as a new
ruleset — one protection system, not two.

| Setting | Value |
| --- | --- |
| Require a pull request before merging | on |
| Required approving reviews | **1** |
| Dismiss stale approvals when new commits are pushed | **on** |
| Require approval of the most recent reviewable push | **on** |
| Require status checks | on — `python pytest`, `golden scanner regression`, `python 3.11 CLI render smoke` |
| Require branches to be up to date | **off** |
| Require conversation resolution | on |
| Require linear history | on |
| **Do not allow bypassing the above settings** | **on** |
| Allow force pushes / deletions | off / off |

`Require branches to be up to date` is deliberately off. With canon advancing
several commits a day and no bypass, requiring it would force a re-update and
re-approval immediately before every merge — recreating the treadmill this change
exists to remove. Drift is caught after the fact by
`tools/canon/landing_reconciler.py`.

## Why ADR-0037's blockers no longer apply

ADR-0037 recorded two account-owner blockers, both now cleared:

1. **Repo plan.** The rulesets API previously returned *"Upgrade to GitHub Pro or
   make this repository public."* `CorwinFoundation` is on GitHub Pro; the
   Rulesets page loads and the settings above were applied.
2. **PAT scope.** `Administration: write` was never granted and is not needed:
   the account owner applied the settings through the web UI. **No token was
   given administrative rights.** In particular `GITHUB_PAT_ARE_SCANNER` was
   *not* revived — BEAAA-24437, BEAAA-24446 and BEAAA-24500 unbound it from
   every seat and both push paths, and that stands.

ADR-0037's Phase 1 and Phase 2 setting tables remain accurate. Only its Blockers
section is superseded.

## The second identity, and why one was unavoidable

GitHub does not permit an account to approve its own pull request. The fleet
pushes as `CorwinFoundation`, which is also the repository owner and the author
of every open pull request, so requiring one approving review with no second
identity would have made every landing impossible.

A dedicated collaborator seat, **`backbond-qa`** (id `319805551`), holds `Write`
access for exactly this purpose. It authors no work. It approves.

## What changes for every operational seat

**The binding QA approval is now a GitHub review, not a Paperclip comment.**

The QA seat posts:

```
POST /repos/CorwinFoundation/are-scanner/pulls/{n}/reviews
  { "commit_id": <head sha>, "event": "APPROVE", "body": <evidence block> }
```

GitHub binds the review to that commit, re-points it through a base merge, and
dismisses it when reviewable code changes. The exact-SHA discipline the company
already required is therefore **enforced by the platform rather than by
convention**. A `422` on that call means the head moved before the post — which
is the check working, not an error to route around.

Consequences for each seat:

- **Any seat that pushes.** You cannot approve your own pull request. Author
  pushes, QA approves. If a branch needs updating, the *author* does it — if the
  approver updates the branch they become the last pusher and their own approval
  stops counting.
- **QA.** Keep writing the evidence block. Post it as a review body. Continuing
  to record it in Paperclip is welcome as supporting evidence; it is no longer
  the gate.
- **CTO.** The co-sign contract is unchanged: required iff the action is a rule
  change. Grant phrasing should name the exact SHA. A comment that *refers* to a
  co-sign without granting one for the landed SHA reads as approval to a human
  and is counted as absent by the reconciler.
- **Everyone.** Merges are squash or rebase. Linear history is enforced, so a
  merge commit onto master is refused.

## What does not change

- Paperclip remains the substantive record. This ADR moves where the *gate*
  lives, not where the evidence lives.
- `merged_sha_on_canon_master_at_close` is still required at closure.
- Retroactive co-sign backfill remains **prohibited** per the 2026-08-19 ruling
  on BEAAA-24491. Nothing here authorises one.
- `tools/canon/landing_reconciler.py` remains valuable: branch protection still
  does not check whether the QA approval cited the SHA that actually landed, and
  it is the only thing that measures the merged-SHA leg.

## Evidence that it holds

- **PR #232** exercised the whole path the same afternoon: pushed by the author,
  self-approval refused, approved by `backbond-qa` against an exact SHA, 19
  checks green, squash-merged.
- **PR #202** had its approval **automatically dismissed** when a code change was
  pushed after approval, and required a fresh one.
- Seven pull requests landed on 2026-08-22 through this path (#216, #217, #202,
  #203, #219, #223, #204), each with a review bound to the SHA that landed.

## Correction required elsewhere

`tools/canon/README.md`, section *"Why it exists"*, states that branch protection
"does not bind the identity every agent pushes with" and that turning
`enforce_admins` on "would stop every landing in the company, so it is sequenced
as a migration rather than a switch." Both statements were true when written and
are false as of 2026-08-22 — the switch was thrown and landings continued the
same afternoon. That section should be updated to point here. The paragraph
immediately following it remains correct and is now the tool's whole
justification.

## Rollback

Fully reversible: clear the settings on the `master` rule. No history is
affected. Removing `backbond-qa` from collaborators would also disable the path,
which is why that seat should not be removed casually.
