---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-13644 converts the BackBond Scanner active-test count into a reproducible registry gate so underwriting totals cannot exceed executable evidence."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-13644", "BEAAA-13642"]
proof_points_cited: ["tools/active_test_registry.py", "tools/active_test_registry_claims.json", "tests/test_active_test_registry_beaaa13644.py", "artifacts/beaaa-13644/active_test_counts.json", "tools/calibration-deltas/2026-07-12-current-master-vs-beaaa13644-active-test-registry.md"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["CTO escalation required: required N=5 gate reports >1 score deltas for G3, M1, and M3 despite no scoring-path edits in this ticket."]
---

# BEAAA-13644 Active-Test Registry Closeout

BLUF: implementation is complete, but merge/closure is blocked on CTO disposition because the required golden N=5 gate surfaced deltas above the ±1 threshold.

## Deliverable

This is a count-gate implementation for the CTO and Scanner Engineer lane that prevents BackBond Scanner published test totals from exceeding active production-executable registry rows within one focused pass.

## Classification

Rule execution under `contracts/scanner-implementation-supervision-v1.md@v1.1`: refactor/internal registry and smoke/count test for an existing operation; no rubric point, pillar weight, scoring math, judge model, contract output-shape, tamper-resistance posture, or customer-data handling change.

## Files Added

- `tools/active_test_registry.py` — deterministic registry builder and count gate over `rubric/probe_mappings/*.yaml`.
- `tools/active_test_registry_claims.json` — published active-test count baseline, currently 102, plus the historic 1,180 claim.
- `tests/test_active_test_registry_beaaa13644.py` — focused gate tests for duplicate IDs, required metadata, canonical pillar names, published-count ceiling, and 1,180 delta reporting.
- `artifacts/beaaa-13644/active_test_counts.json` — machine-readable count output.
- `artifacts/beaaa-13644/active_test_registry.json` — normalized registry rows.

## Count Result

- Registry rows: 109
- Active production-executable rows: 102
- Attestation/non-executable rows: 7
- Published active-test count baseline: 102
- Delta to historic 1,180 claim: -1,078
- By pillar: `BIAS_FAIRNESS=12`, `GOVERNANCE=6`, `OPS=32`, `ROBUSTNESS=59`

## Verification

- `python3 tools/active_test_registry.py --check --json-out artifacts/beaaa-13644/active_test_counts.json --registry-out artifacts/beaaa-13644/active_test_registry.json` — PASS.
- `python3 -m unittest tests.test_active_test_registry_beaaa13644` — PASS, 5 tests.
- `python3 scripts/release_gate_n5.py --baseline current-master --candidate beaaa13644-active-test-registry --today 2026-07-12` — PASS on spread, but reports deltas above ±1 on `G3`, `M1`, and `M3`.

## N=5 Escalation

Required by Scanner Engineer OS: halt merge and escalate when a rule-execution ticket returns a golden-agent delta above ±1. The active-test registry does not import or change scoring code, so the drift appears pre-existing in the current workspace or baseline relationship. CTO must classify whether this is an unrelated existing release-gate drift or requires a broader rule-change/co-sign path before BEAAA-13644 can close.

Operational metadata:

- `scanner_version`: no scanner version bump; registry-only change against current master.
- `corpus_version`: no corpus version bump; release gate used `0.2.0`.
- `probe-set-version`: derived from current `rubric/probe_mappings/*.yaml`; no probe-set behavior changed.
- `rubric_point_id`: all mapped active rows with explicit `dim`; no new rubric IDs.
- `N=5 calibration delta`: `tools/calibration-deltas/2026-07-12-current-master-vs-beaaa13644-active-test-registry.md`.
- `cto_cosign_ref`: pending.

Rollback: remove `tools/active_test_registry.py`, `tools/active_test_registry_claims.json`, `tests/test_active_test_registry_beaaa13644.py`, and `artifacts/beaaa-13644/`.
