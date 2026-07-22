---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-16775 makes the BackBond Scanner production registry count auditable at 304 customer-target runtime identities plus one governance attestation, preserving the scanner evidence layer behind BackBond's trust-layer focus."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-16775", "BEAAA-13761", "BEAAA-16776", "BEAAA-16778"]
proof_points_cited: ["artifacts/beaaa-16775/active-test-registry-summary.json", "artifacts/beaaa-16775/canonical-test-registry.json", "artifacts/beaaa-16775/framework-worker-manifest.json", "tools/calibration-deltas/2026-06-15-beaaa5527-p1-customer-runner-streaming.json"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-16775 Implementation Closeout

BLUF: implementation candidate is ready for the QA exact-SHA gate. The production registry now reports 305 production-counted checks: 304 distinct customer-target runtime identities and one governance attestation. Raw duplicate declarations are preserved as aliases and are not attempted, evidenced, scored, or advertised twice.

## Classification

Rule execution under `contracts/scanner-implementation-supervision-v1@v1.1`: registry plumbing, executor wiring, and smoke/integration coverage under existing rubric points. No rubric point, weight, judge model, scoring math, output JSON field, or tamper-resistance posture changed. `cto_cosign_ref`: not required.

## What Changed

- Added runtime identity reconciliation in `tools/active_test_registry.py`.
- Promoted one canonical executable row per normalized runtime payload; duplicate declarations remain as alias metadata.
- Kept the 100 AIUC Path B reported-not-scored rows excluded from production count.
- Normalized the eight legacy cross-customer rows into production-counted target-bound rows.
- Updated the published active-test claim to 305.
- Wired full-scan expected/attempted/evidence rows to use `runtime_case_identity` when present.
- Added materialized text-compatible execution support in `tools/registered_case_executor.py`.
- Removed the registry-only score persistence skip in `api/scan_handlers.py`; registry-only full scans can now append a score envelope.
- Aligned canonical registry, probe registry, worker manifest, and release gate counts to the same production-count predicate.

## Evidence

- `artifacts/beaaa-16775/active-test-registry-summary.json`: `active_executable_count=305`, `distinct_runtime_case_count=304`, `target_bound_runtime_count=327`, `published_active_test_count=305`.
- `artifacts/beaaa-16775/canonical-test-registry.json`: `unique_active_executable_case_count=305`.
- `artifacts/beaaa-16775/framework-worker-manifest.json`: `active_registry_rows=305`, `release_blocker_count=0`.
- N=5 calibration: `tools/calibration-deltas/2026-06-15-beaaa5527-p1-customer-runner-streaming.json`; B1/G1/G2/M1/M2/M3 all spread 0 and total/pillar delta 0.

## Verification

```text
pytest -q tests/test_active_test_registry_beaaa13644.py tests/test_beaaa16647_aiuc_count_freeze.py tests/test_canonical_test_registry_beaaa13762.py tests/test_framework_worker_registry_beaaa13682.py tests/test_full_scan_executor_beaaa13681.py tests/test_registered_case_execution.py
48 passed in 18.91s

python3 -m tools.active_test_registry --check --json-out artifacts/beaaa-16775/active-test-registry-summary.json --registry-out artifacts/beaaa-16775/active-test-registry-rows.json
PASS; warning table retained for raw duplicate declarations, but strict gate found no duplicate production-counted runtime identity.

python3 -m tools.framework_worker_registry --check --json-out artifacts/beaaa-16775/framework-worker-manifest.json
PASS; active_registry_rows=305; release_blocker_count=0.

python3 -m tools.canonical_test_registry --repo . --emit artifacts/beaaa-16775/canonical-test-registry.json --check
PASS; unique_active_executable_case_count=305.

python3 scripts/beaaa5528_n5_calibration.py
PASS; spread_n5_zero_all_fixtures=True; delta_vs_master_n5=0.
```

## Required Metadata

- `scanner_version`: `1.2.0`
- `corpus_version`: `scanner-test-registry-baseline-2026-07-13`; runtime registry artifacts also report `registry-baseline-*` where derived from current git head.
- `probe-set-version`: `scanner-test-registry-baseline-2026-07-13/v0.1`; active registry claim `active-probe-registry@0.1.0` lineage.
- `rubric_point_id`: `ALL_ACTIVE_DIMENSIONS`; promoted rows retain per-row `risk_or_rubric_id`.
- `N=5 calibration delta`: max total delta 0; max pillar delta 0; spread 0 on B1/G1/G2/M1/M2/M3.
- `upstream_eval_version`: promptfoo `0.115.0`, garak `0.10.0`, inspect-ai `0.3.220`, PyRIT `0.6.0`, llm-guard `0.3.16` + Presidio `2.2.358`.
- `cto_cosign_ref`: not required, rule execution.

## QA Handoff

BEAAA-16776 remains the mandatory QA gate. Do not promote this candidate to a shared or production environment until QA records evidence-backed `APPROVED` on the canonical QA child for the exact candidate SHA.
