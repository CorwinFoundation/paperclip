---
artifact_type: engineering_evidence
owner: scanner_engineer
persona_audience: internal
canonical_terms_only: true
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-19052", "BEAAA-18995"]
version: 0.1
self_audit_passed: true
---

# BEAAA-19052 Durable Canary Lease Consumption

BLUF: The tranche-1 production entrypoint no longer relies on container-local SQLite; it atomically consumes the signed single-use lease in a shared writable PostgreSQL authority whose storage identity, recovery epoch, schema, and health are validated before executor construction.

- Classification: rule execution under the existing-operation and behavior-preserving implementation rows in `scanner-implementation-supervision-v1@v1.1`; score, rubric, judge, weight, and authorization rules are unchanged.
- Contract: `are-scanner-api-v1@v1.3.0`.
- Scanner version: `0.5.0`.
- Corpus version: `fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988`.
- Probe-set version: three tranche-1 `tool_misuse_*@0.1.0` families.
- `rubric_point_id`: `ROBUSTNESS.TOOL_MISUSE.*`.
- `cto_cosign_ref`: not required for this rule-execution repair; exact-SHA CTO review remains a promotion gate.
- Shared uniqueness: `scope_sha256` and `lease_id` are unique in the shared primary; `INSERT ... ON CONFLICT DO NOTHING RETURNING` is the atomic fence across replicas, processes, hosts, and restarts.
- Continuity: the runner requires operator-pinned `storage_identity` and `continuity_epoch`; recovery/restore must rotate the epoch. Missing schema, reinitialized storage, stale epoch, read replica, read-only primary, driver failure, or outage fails closed.
- Verification: `45 passed` across the focused authority and existing tranche-1 canary suites. Restart/replica replay, 8-way concurrency, missing/reinitialized storage, stale restore epoch, read-only recovery, and outage all reject without a second consumption.
- Target/judge ceiling: one lease can create at most one execution attempt; the existing burn-before-execution path retains the cumulative target-call ceiling of 180 and judge-call ceiling of 0.
- Rollback: restore the prior candidate only as a non-executable artifact; it remains rejected and must not be promoted or called.

No merge, deployment, promotion, or production canary call occurred. Fresh exact-SHA CI, CTO, QA, Compliance, Underwriting, and Founder gates remain required.
