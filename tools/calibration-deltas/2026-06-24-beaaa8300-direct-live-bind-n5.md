---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-8300 verifies the BackBond Scanner direct live-bind egress patch preserves reproducibility while enabling the CTO-approved host nft lifecycle."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-8300", "BEAAA-8263", "BEAAA-7910", "BEAAA-7256"]
proof_points_cited: ["Focused pytest gate 34 passed", "N=5 replay B1/G1/G2/M1/M2/M3 spread 0", "CTO co-sign BEAAA-8263 interaction 8b22133d-e86a-41d0-8ec3-72e5c2289c5d"]
version: "0.1"
prior_version: null
self_audit_passed: true
escalations_noted: ["Tamper-resistance posture change classified rule-change per scanner-implementation-supervision-v1@v1.1; CTO co-sign accepted before merge/deploy."]
---

# BEAAA-8300 Direct Live-Bind N=5 Receipt

BLUF: BEAAA-8300 preserves `are-scanner-api-v1@v1.0` output shape and scoring semantics. The change only gates a parent-owned, scan-scoped host nft rule for the declared live-bind endpoint while keeping the in-process `ScopedEgressAllowlist` and Scorer deny-all posture intact.

## Metadata

- `scanner_version`: `0.4.2`
- `corpus_version`: `0.1.0`
- `probe-set-version`: `none; host egress plumbing only`
- `rubric_point_id`: `none; live-bind transport path only`
- `cto_cosign_ref`: `BEAAA-8263 interaction 8b22133d-e86a-41d0-8ec3-72e5c2289c5d accepted 2026-06-24T19:33:08Z`
- Classification: rule-change, `scanner-implementation-supervision-v1@v1.1` row `Tamper-resistance posture change`
- Contract: `are-scanner-api-v1@v1.0`; no response-shape, rubric, scoring math, judge, corpus, or probe-score change

## Verification

- `python3 -m py_compile harness/egress.py api/scan_runtime_factory.py tests/test_beaaa8263_direct_live_bind.py`
- `pytest -q tests/test_beaaa8263_direct_live_bind.py tests/test_egress.py tests/test_live_probe_workers_beaaa542.py::test_prober_egress_scope_still_works_single_host tests/test_live_probe_workers_beaaa542.py::test_scorer_egress_scope_denies_all_hosts`
- Result: `28 passed`

## N=5 Golden Replay

Command:

```bash
python3 scripts/beaaa5528_n5_calibration.py
```

The script filename is legacy from an earlier transport-only harness; BEAAA-8300 reused the same deterministic score replay after patching it to pass current Supreme Law pins and fixture applicability decisions.

| Fixture | Expected total | Observed totals N=5 | Spread | Max locked-total delta | Max pillar delta |
|---|---:|---|---:|---:|---:|
| B1 | 39 | 39, 39, 39, 39, 39 | 0 | 0 | 0 |
| G1 | 79 | 79, 79, 79, 79, 79 | 0 | 0 | 0 |
| G2 | 77 | 77, 77, 77, 77, 77 | 0 | 0 | 0 |
| M1 | 58 | 58, 58, 58, 58, 58 | 0 | 0 | 0 |
| M2 | 48 | 48, 48, 48, 48, 48 | 0 | 0 | 0 |
| M3 | 54 | 54, 54, 54, 54, 54 | 0 | 0 | 0 |

Gate result: spread 0 on every locked golden; maximum locked-total delta 0; within the `are-scanner-api-v1@v1.0` +/-1 reproducibility band.

## Self-Audit

- Glossary: internal legacy contract identifiers retained only where grandfathered; no deprecated external naming introduced.
- Brand gate: not triggered; internal Scanner implementation receipt.
- FAL / house-style: BLUF first, specific evidence, no external customer/carrier naming.
- Wrap-don't-fork: not applicable; no upstream eval implementation added.
