---
artifact_type: engineering_evidence
owner: scanner_engineer_2
persona_audience: internal
canonical_terms_only: true
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-19024", "BEAAA-18995", "BEAAA-19052", "BEAAA-19093"]
version: 0.4
prior_version: 0.3
self_audit_passed: true
---

# BEAAA-19024 Promotion Authority Repair

BLUF: PR #166 accepts only independently issued Ed25519 authority and atomically consumes each exact lease scope once in a shared writable PostgreSQL authority before any target call or evidence write.

- Classification: rule change under the tamper-resistance posture row in `scanner-implementation-supervision-v1@v1.1`; this strengthens tamper resistance and requires CTO co-sign. Founder production authorization remains a separate exact-scope signed input.
- Authority: the runner contains verification-only Ed25519 public material. No caller-supplied signing secret or minting path exists.
- Consumption: the unique scope binds `lease_id`, signature, commit, immutable image, tenant, and production environment. `INSERT ... ON CONFLICT DO NOTHING RETURNING` fences concurrent double-spend across replicas, processes, hosts, and restarts.
- Continuity: the runner validates the operator-pinned storage identity, continuity epoch, schema, and writable-primary health before executor construction. Recovery or restore must rotate the epoch.
- Failure posture: replay, concurrent replay, unknown or revoked issuer, forged lease, forged Founder authority, missing or reinitialized storage, stale recovery epoch, read replica, read-only primary, driver failure, and authority outage all fail closed. Rejected attempts make zero target calls and create zero evidence records.
- Contract pin: the runtime manifest emits exactly `are-scanner-api-v1@v1.0`, with an exact top-level key-set assertion guarding response-shape stability.
- Canary-runner/content pins: `4c3c15d9688c5ebc8fdb052e06194c5d6fb3efdf7923779bbb318200ea3ff959` / `819dfc88451735899a88e025f43530a41900572473373ce0c86b07c47fb38bb2`. The content pin excludes measured `budget.elapsed_seconds`.
- Scanner version: `0.5.0`.
- Corpus version: `fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988`.
- Probe-set version: three tranche-1 `tool_misuse_*@0.1.0` families.
- `rubric_point_id`: `ROBUSTNESS.TOOL_MISUSE.*`; no scoring or rubric edit.
- N=5 calibration delta: absolute Δ=0 for total, every pillar, band, eligibility, pricing, premium, policy behavior, and published weighted/countable totals.
- Verification: `63 passed` across the runner, shared-authority, and observational isolation suites, including partial-execution lease burn, restart and replica replay, 8-way concurrency, continuity failures, read-only recovery, and authority outage.
- Rollback: disable the observational canary and restore the retained pre-candidate image and configuration. Every prior PR #166 candidate remains a non-executable rejected artifact and must not be promoted or called.

No merge, deploy, promotion, evidence mutation outside tests, or production canary call occurred. Exact-SHA QA `APPROVED` on the canonical QA child, Compliance rebind, CTO co-sign, and Founder production sign-off remain required.
