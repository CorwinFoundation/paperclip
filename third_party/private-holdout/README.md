# Attacker v1 private hold-out

Generate a rotation by calling `build_inventory(repo_root, "YYYY-MM")` from
`harness.attacker_goals`. The split is deterministic for auditability and is
re-keyed by calendar month. Exactly 20% of every underwriting family is written
here; the remaining 80% is written under `generated/attacker-v1/YYYY-MM/`.

Files below this directory are evaluation-only. Report builders, customer
exports, public artifacts, and frozen-success packs must never read or copy
them. A goal moves into a family pack only after a successful live attack; an
unattempted or failed hold-out goal remains private evidence and is not a test.
