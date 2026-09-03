# QA Authority shadow

BEAAA-29079 steps 1–3 only. The workflow is disabled unless the base-branch
repository variable `QA_AUTHORITY_SHADOW_ENABLED` is exactly `true`; it does
not replace or satisfy the required `qa-review-evidence` check.

The public edge must not receive `PAPERCLIP_API_KEY`. Paperclip corroboration
runs separately on the company VM, bound to loopback, using
`paperclip_reader.py`. Its allowlist is GET-only and closed by default.

The shadow database is a single SQLite origin. Back up the database file and
restore it cold before restarting the service. No standby, multi-region, HA
database, or automated outage bypass is part of this lane.

