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
