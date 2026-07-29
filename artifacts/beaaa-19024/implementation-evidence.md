---
artifact_type: engineering_evidence
owner: scanner_engineer_2
persona_audience: internal
canonical_terms_only: true
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-19024", "BEAAA-18995"]
version: 0.1
self_audit_passed: true
---

# BEAAA-19024 Promotion Authority Repair

BLUF: PR #166 now fails closed before calls or writes unless its authenticated lease matches the runtime candidate SHA, digest-qualified image, production environment, runtime tenant, exact three-family/36-case identity hash, 180/0 call ceilings, issue/approval identity, and validity window.

- Classification: rule change under the tamper-resistance posture row in `scanner-implementation-supervision-v1@v1.1`; CTO co-sign and Founder authorization remain pending.
- Scanner version: `0.5.0`.
- Corpus version: `fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988`.
- Probe-set version: three tranche-1 `tool_misuse_*@0.1.0` families.
- `rubric_point_id`: `ROBUSTNESS.TOOL_MISUSE.*`; no scoring or rubric edit.
- N=5 calibration delta: absolute Δ=0 for total, every pillar, band, eligibility, pricing, premium, policy behavior, and published weighted/countable totals.
- Verification: `44 passed in 63.37s` for the runner and observational isolation suites.
- Rollback: restore rejected draft SHA `bce3cebb4f08b93e6abbfe74db7b6b6d056c4c31`; it remains ineligible for execution.

No merge, deploy, promotion, evidence mutation outside tests, or production canary call occurred. Exact-SHA QA `APPROVED`, Compliance, Underwriting, CTO, and Founder gates remain required.
