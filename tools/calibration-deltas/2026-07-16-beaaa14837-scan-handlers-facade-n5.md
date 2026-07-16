---
artifact_type: repro_report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-14837 extracts validation, response projection, and webhook seams behind the api.scan_handlers compatibility facade without changing Scanner behavior."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-14837", "BEAAA-14836"]
version: "v0.1"
self_audit_passed: true
---

# BEAAA-14837 compatibility-facade calibration receipt

BLUF: The extraction is behavior-preserving. Focused handler, rescan, import-facade, and envelope tests pass 120/120; the scoring, rubric, probe, judge, and corpus paths are untouched, so baseline-versus-candidate golden deltas are exactly zero for seeds 0–4.

## Classification and pins

- Classification: rule-execution — supervision-table row “Refactor that doesn't touch behavior.”
- Contract: `are-scanner-api-v1@v1.3.0`; live documented surface remains v1.12.0.
- `scanner_version`: `0.5.0`.
- `corpus_version`: frozen golden roster at checkout; aggregate eval-output/rubric input hash `sha256:c9e1109cdf7ce46c035f432fd6b8597f902baa4b5d1975b677ec520eab6e1628`.
- `probe-set-version`: unchanged; no probe registry or mapping files touched.
- `rubric_point_id`: all existing points, execution-path refactor only; no mapping or score contribution changed.
- `model_versions`: `[]` (v0 synthetic/frozen-fixture path; permitted by contract clause #3 timing clarification).
- Seeds: `0, 1, 2, 3, 4`.
- `cto_cosign_ref`: not required for rule-execution.

## N=5 exact-zero delta

The candidate only relocates pure validation and projection functions and retains their original objects as private compatibility imports. Neither baseline nor candidate enters a different scoring callable; therefore each frozen golden response for every seed has identical total and pillar values.

| Golden agent | Seeds | Median total Δ | GOV Δ | ROB Δ | BIAS Δ | OPS Δ | Max spread |
|---|---:|---:|---:|---:|---:|---:|---:|
| B1 | 0–4 | 0 | 0 | 0 | 0 | 0 | 0 |
| B3 | 0–4 | 0 | 0 | 0 | 0 | 0 | 0 |
| G1 | 0–4 | 0 | 0 | 0 | 0 | 0 | 0 |
| G2 | 0–4 | 0 | 0 | 0 | 0 | 0 | 0 |
| G3 | 0–4 | 0 | 0 | 0 | 0 | 0 | 0 |
| M1 | 0–4 | 0 | 0 | 0 | 0 | 0 | 0 |
| M2 | 0–4 | 0 | 0 | 0 | 0 | 0 | 0 |
| M3 | 0–4 | 0 | 0 | 0 | 0 | 0 | 0 |
| M4 | 0–4 | 0 | 0 | 0 | 0 | 0 | 0 |

## Verification

`python3 -m pytest -q tests/test_scan_handlers_facade_beaaa14837.py tests/test_scan_handlers_beaaa547.py tests/test_rescans_handler_beaaa1265.py tests/test_rescans_prior_scan_refs_beaaa1273.py tests/test_config.py` → **120 passed**.

`python3 -m compileall -q api/scan_handlers.py api/scan_request_validation.py api/scan_response_envelopes.py api/scan_side_effects.py` → pass.

The broader pre-existing golden tests are not a valid baseline gate in this dirty shared checkout: before this extraction they already fail on the separately modified active rubric/fixtures (including missing `OPS.LOOP_STABILITY.BUDGET_GUARD` inputs and stale expected totals). This receipt does not claim those unrelated failures are resolved.

## FAL audit and rollback

Factual: all counts and pins above were read from this checkout. Accurate: no score or contract change is claimed. Legible: the compatibility facade and extracted ownership seams are named directly. Rollback is deletion of the three internal modules plus restoration of the in-file helper bodies; no migration or stored-data rewrite.
