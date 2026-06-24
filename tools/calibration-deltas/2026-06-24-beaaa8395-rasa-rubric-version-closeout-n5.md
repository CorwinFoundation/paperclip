---
artifact_type: report_card
owner: cto
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "BEAAA-8395 closes the Rasa grade-card rubric-version drift without changing BackBond Scanner rules or score paths."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-8395", "BEAAA-8389", "BEAAA-7653"]
proof_points_cited: ["pytest -q tests/test_beaaa8389_rasa_consolidated_grade_card.py", "harness.golden_set.n5_band.run_l1_n5(today='2026-06-24')", "artifacts/beaaa-8389/consolidated_summary.json"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule-execution per scanner-implementation-supervision-v1@v1.1 row 'Smoke test for an existing operation' / customer onboarding harness; no rubric point, scoring math, judge model, contract output shape, or tamper-resistance change."]
---

# BEAAA-8395 Calibration Delta — Rasa Rubric-Version Closeout

BLUF: pass. The BEAAA-8389 Rasa runner now sources `rubric_version` from `rubric/rubric.yaml`, which is `1.2.0`; the fabricated `1.4.0` value no longer appears in generated BEAAA-8389 outputs. Golden-set N=5 remains in band with max BackBond Score drift 0.

- Contract: `are-scanner-api-v1@v1.3.0`
- Supervision: `scanner-implementation-supervision-v1@v1.1`
- scanner_version: `0.4.1`
- corpus_version: `rasa-consolidated-red-battery@2026-06-24`; golden replay uses mixed fixture pins
- probe-set-version: `rasa-red-battery-detector-harness@2026-06-24`
- rubric_point_id: `P1.1, ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL, ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER, P3.1, P3.2, P3.3, ROBUSTNESS.PII.SCAN_TIME_DETECTION, P4.4`
- rubric_version: `1.2.0`
- cto_cosign_ref: `not required; rule-execution`
- merged_sha_on_canon_master_at_close: populated in BEAAA-8395 closeout comment after commit

## Verification

1. Rasa card acceptance:
   `pytest -q tests/test_beaaa8389_rasa_consolidated_grade_card.py`
   Result: `1 passed`.

2. Golden-set N=5:
   `python3 - <<'PY' ... run_l1_n5(today='2026-06-24') ... PY`
   Result: `N=5 | baselined=8/9 | pending=1 | max ARE drift=0 | breaches=0 | PASS`.

3. Stale-version search:
   Pattern: `1\.4\.0`, scoped to `scripts/beaaa8389_rasa_consolidated_grade_card.py`, `tests/test_beaaa8389_rasa_consolidated_grade_card.py`, and `artifacts/beaaa-8389`.
   Result: only the negative assertion in `tests/test_beaaa8389_rasa_consolidated_grade_card.py` remains.

4. Brand Backbone gate:
   `_default/scripts/verify-brand-backbone.sh`
   Result: `OK: Brand Backbone v1.4 SHA matches pin (241d1082...)`.

## Interpretation

The change does not alter scoring, rubric membership, judge selection, pricing, admittance, or contract envelope shape. It removes a stale handwritten summary constant and hardens the BEAAA-8389 test so persisted summary JSON, closeout markdown, and seed-0 envelope must agree with the active rubric file.
