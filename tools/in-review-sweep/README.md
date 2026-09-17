# In-Review Aging Sweep

Canonical source for the BEAAA-31939 routine (4x/day, Pillar D of BEAAA-2702).
This directory is the source of truth. The copy the routine executes lives at
`workspaces/<agent-uuid>/tools/in-review-sweep/sweep.py` on the host and is an
install, not the original — the original was authored host-local on 2026-07-01
and was lost, which is what BEAAA-31940 fixed.

## Restore the host copy

    cd /mnt/paperclipdata/dot-paperclip/instances/default/workspaces/<agent-uuid>
    git -C are-scanner show origin/master:tools/in-review-sweep/sweep.py \
      > tools/in-review-sweep/sweep.py

`state.json` is the 24h ping dedup ledger and is host-local by design — it is
run state, not source. `state.json.seed` here is the seed shape to start from if
the ledger is ever lost. Starting from an empty ledger is safe only when nothing
is currently in the 24-72h band; otherwise the first run re-pings the whole band.

## Run

    python3 sweep.py             # live
    python3 sweep.py --dry-run   # bucket and print, post nothing
    python3 sweep.py --seed      # record the current band as pinged, post nothing

Requires `PAPERCLIP_API_KEY`, `PAPERCLIP_COMPANY_ID`, `PAPERCLIP_API_URL`. The
script refuses to run without a key: an unauthenticated POST to this API does not
401, it posts as `user local-board`.

## API notes that cost time during the rebuild

- Issue lists are company-scoped in the path: `/api/companies/{id}/issues`.
  A bare `/api/issues?companyId=` returns 400.
- `/api/companies/{id}/issues/{BEAAA-*}` 404s on the identifier. Resolve to the
  UUID by paging the list; `?search=` on the list is ignored.

## Fragile-confirmation audit (BEAAA-33595)

Every run also classifies the pending `request_confirmation` cards on the rows it
sweeps. A confirmation created with the default `supersedeOnUserComment: true` is
destroyed by the **next board/user comment on its own issue** — `status: expired`,
`result.outcome: superseded_by_comment`, `respondedAt: null`. From the outside the
row still looks like it has a live reviewer route, so a decision window can close
with nobody told. Measured three times on 2026-09-17: `9839cb06` (BEAAA-29930)
expired 1s after a `local-board` comment; `5bdc8e0a` (BEAAA-30912) and `598ecccf`
(BEAAA-29930) expired in the same second as theirs. BEAAA-29748 makes
`supersedeOnUserComment: false` standing for anything acting as a gate.

The summary grows one clause when any are found:

    Fragile confirmations: N (already superseded: M).

`M` counts cards that are `pending` in name only — a board/user comment already
postdates them. `ask_user_questions` does not carry the flag and is not reported:
`8c16a8af` (BEAAA-33592) took two board comments and stayed pending.

**Scope limit, stated out loud:** the audit sees only the `in_review` rows this
routine already enumerates. A fragile card on a `blocked` or `in_progress` row is
not reported — e.g. `45973c28` on BEAAA-29873 (`blocked`) is fragile today and does
not appear in the count. Widening the enumeration is a separate decision, not an
oversight.

## Reviewer resolution reads the newest comments

The comments API returns **newest-first** (measured on BEAAA-31040: 21:17, 21:10,
21:09, 21:06, 09-16 13:29). v0.2 sliced `[-3:][::-1]`, i.e. the three *oldest*
comments of the fetched page, which is not where a park lands; on rows at the
25-comment fetch cap the newest comments were never examined at all. Now reads
`[:3]`. This changed no ping target on the 17 rows live at the time of the fix —
the defect was latent, not actively misrouting — but 16 of those 17 have more than
three comments, so the two slices were reading different text.
