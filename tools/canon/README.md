# Canon master-landing evidence reconciler

`landing_reconciler.py` checks, after the fact, whether each commit that landed
on `refs/heads/master` carried the evidence the company requires for it. Three
legs, and they are not the same for every landing:

| Leg | Applies to | Source |
| --- | --- | --- |
| QA `APPROVED` naming an exact SHA of the landing | every landing | `qa-gate-lifecycle` — landing on canon is a shared promotion |
| CTO merge co-sign naming an exact SHA of the landing | landings whose resolving ticket classifies itself a **rule change** | `scanner-implementation-supervision-v1` v2.0: `cto_cosign_ref` required *iff the action is a rule change* |
| `merged_sha_on_canon_master_at_close`, reachable from canon HEAD | every landing | v2.0, on both rule-change and rule-execution tickets that ship code — the gate matches commit-shape, not classification-shape |

The legs are counted separately. They fail for different reasons and land on
different owners, and folding them into one `unreconciled` total is what turned
a single-leg gap into a company-wide-indiscipline reading (ruling on
BEAAA-24491).

It is a **detective control**. It runs on a schedule, it reports, and it blocks
nothing. It cannot prevent an undisciplined landing; it makes one visible.

## Why it exists

**Updated 2026-08-22.** Branch protection on `CorwinFoundation/are-scanner` now
binds every identity, including the one the agent fleet pushes with.
`enforce_admins` is on, a pull request is required, and one approving review from
an identity other than the pusher is required. See
`decisions/ADR-0072-master-branch-protection-applied.md`.

What this section used to say — that `enforce_admins` was off, that the required
checks were advisory in effect for the fleet identity, and that flipping the
switch "would stop every landing in the company, so it is sequenced as a
migration rather than a switch" — was true when written and is no longer true.
The switch was thrown on 2026-08-22 and seven pull requests landed the same
afternoon. Prior reasoning is retained at
`ADR-BEAAA-24442-canon-master-enforcement-surface.md` and the ruling on
BEAAA-24442.

Paperclip remains the substantive record. What moved is where the *gate* lives.

**That change does not retire this tool, for the reason the original text already
gave:** branch protection still does not check whether the QA approval cited the
SHA that actually landed, and nothing else measures the
`merged_sha_on_canon_master_at_close` leg at all. What has changed is that the QA
approval is now a GitHub review carrying `commit_id`, so the failure class
`QA APPROVED names predecessor` cannot recur on anything landing through the PR
path. Expect that leg to fall on its own. The other two will not.

## Running it

```
export PAPERCLIP_API_URL=... PAPERCLIP_API_KEY=... PAPERCLIP_COMPANY_ID=...
python3 tools/canon/landing_reconciler.py --since 30d
```

Exit status is `1` if any landing in the window is `unreconciled`, `0`
otherwise, `2` on a configuration problem. Useful flags:

| Flag | Effect |
| --- | --- |
| `--since` | window start: `30d`, `2w`, `12h`, or an ISO date. Default `30d`. |
| `--until` | window end as an ISO date. Default now. |
| `--format` | `table` (default), `markdown` for pasting into an issue, `json`. |
| `--exempt-before` | ISO date before which landings are `exempt`, with that reason printed. |
| `--cache-dir` | where the blobless bare mirror of the canon branch lives. |
| `--no-fetch` | use the mirror as-is. Offline reruns and tests. |
| `--no-fail-on-unreconciled` | report without setting a non-zero exit. |

Every call it makes is a read: `git clone`/`git fetch` against GitHub, `GET`
against Paperclip. It writes only to `--cache-dir`.

## What a landing is

One first-parent step of `master`. For a merge commit the landing is the merge
**plus its non-master parents**, because that is the unit the co-sign is given
on — the co-sign names the candidate branch tip, and the merge that lands it
necessarily has a different SHA. Commits that arrived inside a merged branch are
members of that landing, not landings of their own.

So an approval naming either the merge SHA or the candidate tip reconciles the
landing. An approval naming an intermediate commit of the merged branch does
not: that is a predecessor, and the tool says so by SHA.

## Reading the table

| Column | Meaning |
| --- | --- |
| `SHA` | the landing's head commit |
| `DATE` | commit date of that head |
| `ISSUE` | the Paperclip issue the landing belongs to |
| `TICKET` | how that issue classifies itself: `change`, `exec`, or `?` |
| `CO-SIGN` | comment id of the CTO co-sign grant; `n/a` where the ticket is not a rule change and the contract asks for none |
| `QA` | comment id of the QA `APPROVED` |
| `MERGED-SHA` | the SHA the closure cited, once resolved against canon |
| `CLASS` | `reconciled`, `unreconciled`, or `exempt` |
| `REASON` | for `unreconciled`, exactly what is missing; for `exempt`, why |

`n/a` and `—` are different states in the `CO-SIGN` column. Printing "not
required" the same way as "required and absent" is how a correctly scoped
column reads as a gap.

The summary above the table breaks the count out by leg, and states how many
resolving tickets are rule change, rule execution, and unclassified — a co-sign
count means nothing without the size of the slice it is measured over.

The issue is resolved from a ticket id in the commit message, falling back to
asking Paperclip which issue names the SHA and then verifying that mention
rather than trusting search rank. Evidence is then searched across the issue's
whole neighbourhood — its ancestors and the tickets it references — because the
co-sign lives on the implementation ticket while the `APPROVED` lives on the
canonical QA-gate child.

## How the classification is read

The contract's operational-seat guarantee is "classify before action, with the
table row referenced", so the classification is read **off the ticket** rather
than guessed from the diff. The tool accepts a field (`classification: rule
execution`, and the corrected form `… -> **rule change**`) or a declaration
whose subject is that ticket ("this ticket is a rule change", "reclassified
BEAAA-23857 as a rule change"). The last declaration in time wins, so a
reclassification recorded later reads as the current value.

A ticket that quotes the classification table without classifying itself is
left `unclassified`, and **an unclassified ticket is never charged a missing
co-sign.** This is the opposite posture to the co-sign matcher, deliberately:
there, an ambiguous comment must not stand in for a grant; here, an ambiguous
ticket must not manufacture a requirement. v2.0 puts the requirement on rule
changes, so a co-sign is only missing where the ticket says one was owed.

## The merged-SHA leg

`merged_sha_on_canon_master_at_close` is verified with `git merge-base
--is-ancestor <cited> <canon HEAD>` — the same proof routine `161a7f8a` already
runs nightly over `security`-labelled closures. Reusing that shape rather than
inventing a second one is deliberate; two reachability checks that disagreed
would be worse than none.

`<canon HEAD>` is the mirror's `refs/heads/master`, force-updated from `origin`
on every run. It is never a local branch that may sit behind the remote — that
staleness is the live defect BEAAA-24536 F1 raises against the contract's own
wording, and it flags correctly merged work Founder-tier. If the head cannot be
resolved the run exits `2` rather than reporting every landing unreachable.

The field name is matched exactly, allowing only the separators a closure
comment actually writes it with. Prose about "the merged sha" is not the field;
reading it as one would pass a landing that recorded no closure proof at all.

Two exemption rules exist, and both print their reason:

- `--exempt-before <date>` — landed before the control took effect.
- a landing whose tree is identical to its first parent's, which put nothing new
  on canon.

A landing that resolves to no Paperclip issue at all has no closure record to
check, so every leg fails for one upstream reason. It is tagged `unresolved` and
counted as a subset of the legs it forces, not as a fourth leg — the counts are
not meant to sum to the `unreconciled` total.

There is no allowlist and no suppression file. A landing that cannot be
reconciled is a finding.

## What it can get wrong

Co-signs are free text, so deciding whether a CTO comment *grants* one or merely
*refers* to one is a heuristic. It errs towards reporting `unreconciled`: a
comment that mentions a co-sign without granting one for an exact SHA is
downgraded, and the reason names the comment id so a reader can overrule it.
Erring the other way would let a closure report stand in for the gate — the
observed failure mode, since a merge report routinely cites the co-sign it is
reporting on.

Classifications are free text too, and the tool reads them conservatively in the
other direction. A ticket that is genuinely a rule change but never says so is
read `unclassified`, and its co-sign column is not evaluated — so the co-sign
count is a floor, not a total. That is the intended error: over-reporting this
column is the failure the re-calibration exists to remove, and the
`unclassified` count is printed next to it so the floor is visible as one.

The tool does not read `check-runs`: `GET /repos/.../commits/{sha}/check-runs`
returns 403 for the deploy PAT. Use `GET /actions/runs?head_sha=` if you need
check state. It does not verify commit signatures, because
`required_signatures` is off on this repo and nothing cryptographically binds a
landed commit to the agent credited with it.

## Tests

```
python3 -m pytest tests/test_canon_landing_reconciler.py
```

The git-layer tests build a throwaway repository with a direct push and a merge
and mirror it the way the tool does. No test asserts a landing count or a named
failing SHA — the tool queries the range, and freezing today's answer would go
red the next time someone lands correctly.
