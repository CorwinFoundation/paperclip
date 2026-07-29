---
artifact_type: engineering_evidence
owner: scanner_engineer_2
persona_audience: internal
canonical_terms_only: true
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-19024", "BEAAA-18995"]
version: 0.3
prior_version: 0.2
self_audit_passed: true
---

# BEAAA-19024 Promotion Authority Repair

BLUF: PR #166 now accepts only independently issued Ed25519 authority and atomically consumes each exact lease scope once in a durable SQLite ledger before any target call or evidence write.

- Classification: rule change under the tamper-resistance posture row in `scanner-implementation-supervision-v1@v1.1`; this strengthens tamper resistance and requires CTO co-sign. Founder production authorization remains a separate exact-scope signed input.
- Authority: the runner contains verification-only Ed25519 public material. No caller-supplied signing secret or minting path exists.
- Consumption: the unique scope binds `lease_id`, signature, commit, immutable image, tenant, and production environment. `BEGIN IMMEDIATE` plus a primary-key insert prevents concurrent double-spend; WAL and `synchronous=FULL` provide durable replay state.
- Failure posture: replay, concurrent replay, unknown/revoked issuer, forged lease, forged Founder authority, and ledger failure all fail closed. Rejected attempts make zero target calls and create zero evidence records.
- Contract pin: the runtime manifest emits exactly `are-scanner-api-v1@v1.0`, with an exact top-level key-set assertion guarding response-shape stability.
- Canary-runner/content pins: `d22b7ad8f230706cf3b1aa410bec395535645dee3632a7829353f173c6c6a672` / `972e7fd61553ab4548105bce2e0d5495e564fdb8bc733156969618d9288f9e28`.
- Scanner version: `0.5.0`.
- Corpus version: `fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988`.
- Probe-set version: three tranche-1 `tool_misuse_*@0.1.0` families.
- `rubric_point_id`: `ROBUSTNESS.TOOL_MISUSE.*`; no scoring or rubric edit.
- N=5 calibration delta: absolute Δ=0 for total, every pillar, band, eligibility, pricing, premium, policy behavior, and published weighted/countable totals.
- Verification: `55 passed` for the runner and observational isolation suites, including partial-execution lease burn.
- Rollback: restore rejected draft SHA `bce3cebb4f08b93e6abbfe74db7b6b6d056c4c31`; it remains ineligible for execution.

No merge, deploy, promotion, evidence mutation outside tests, or production canary call occurred. Exact-SHA QA `APPROVED` on the canonical QA child, Compliance rebind, CTO co-sign, and Founder production sign-off remain required.
