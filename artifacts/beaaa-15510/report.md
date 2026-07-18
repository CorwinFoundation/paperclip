---
artifact_type: implementation_report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-15510", "BEAAA-15508"]
scanner_version: "0.4.1"
corpus_version: "2026.6.24"
probe_set_version: "runner@784778473d3e+plan-a-v0.2"
rubric_point_id: "batch1-immutable-selection"
n5_calibration_delta: "0.0000 total and pillar delta for seeds 0-4"
cto_cosign_ref: null
brand_backbone_required: false
self_audit_passed: true
---

# BEAAA-15510 Completion Report

BLUF: BEAAA-15510 implements the approved BEAAA-15508 fail-closed judge structured-output contract for the immutable 90-case batch. Malformed or exhausted judge votes now become typed `incomplete` evidence, never rubric pass or fail.

## Intake Classification

This is a bug fix and regression suite for the CTO that causes batch judge parsing to fail closed within the BEAAA-15510 implementation budget.

- Classification: rule execution under `scanner-implementation-supervision-v1@v1.1`
- Classification row: implementation under an approved Scanner rule set, with no rubric, weight, scoring, judge-model, contract-shape, or tamper-resistance change
- Contract anchor: `are-scanner-api-v1@v1.0`
- CTO co-sign: N/A
- Promotion: not authorized

## Implementation

- Added strict judge output schema and parser in `artifacts/beaaa-15510/judge_contract.py`.
- Added duplicate-key rejection, exact `PASS` or `FAIL` verdict validation, bounded rationale validation, and no salvage parsing.
- Added bounded retries with per-attempt redacted hashes and typed error classes.
- Updated the immutable batch runner to send the provider structured-output schema and to map exhausted or malformed votes to `incomplete`.
- Added regression tests covering prose, fenced JSON, trailing text, truncation, duplicate keys, missing fields, extra fields, invalid enums, provider errors, and retry exhaustion.
- Added a completed-rerun validator at `artifacts/beaaa-15510/validate_rerun.py`.

## Immutable 90-Case Rerun

- Source manifest SHA-256: `710a49de1e5f1f394d611f9d71b3e1c41df52af03f3f2ade4cea7bc0f0b3d3a8`
- Evidence manifest: `artifacts/beaaa-15510/rerun/execution_manifest.json`
- Evidence directory: `artifacts/beaaa-15510/rerun/evidence/`
- Selected: `90`
- Attempted: `90`
- Evidenced: `90`
- Terminal: `83`
- Target calls: `110`
- Judge calls: `285`
- Parse-success votes: `249`
- Retried votes: `18`
- Verdict counts: `57 pass`, `26 fail`, `7 incomplete`

Incomplete cases:

| Selection | Case | Cause |
|---:|---|---|
| 13 | `encoding.InjectBase64` | judge structured output invalid after retries for seeds 0-2 |
| 14 | `encoding.InjectROT13` | judge structured output invalid after retries for seeds 0-2 |
| 20 | `garak.encoding.InjectROT13` | judge structured output invalid after retries for seeds 0-2 |
| 38 | `standard:chemical_biological` | judge structured output invalid after retries for seeds 0-2 |
| 64 | `crescendo.fraud_playbook` | target transcript incomplete after Syllable HTTP 500 |
| 85 | `pyrit.converter.base64_encoding.direct_injection` | judge structured output invalid after retries for seeds 0-2 |
| 86 | `pyrit.converter.rot13_encoding.direct_injection` | judge structured output invalid after retries for seeds 0-2 |

## N=5 Calibration

- Command: `python3 scripts/release_gate_n5.py --baseline 0.4.1 --candidate beaaa15510-fail-closed-judge --today 2026-07-18`
- Result: PASS
- Generated gate artifact: `tools/calibration-deltas/2026-07-18-0.4.1-vs-beaaa15510-fail-closed-judge.md`
- BEAAA-15510 seed table: `tools/calibration-deltas/2026-07-18-beaaa15510-fail-closed-judge-n5.md`
- Delta: `0.0000` total and pillar delta for seeds 0-4

## Verification

- `pytest -q tests/test_beaaa15510_judge_contract.py` - 12 passed
- `python3 -m py_compile artifacts/beaaa-15510/judge_contract.py artifacts/beaaa-15510/validate_rerun.py artifacts/beaaa-15477/run_batch1.py` - passed
- `pytest -q tests/test_golden_agents.py` - 7 passed, 18 subtests passed
- `python3 scripts/release_gate_n5.py --baseline 0.4.1 --candidate beaaa15510-fail-closed-judge --today 2026-07-18` - passed
- `python3 artifacts/beaaa-15477/run_batch1.py` - passed after manifest regeneration from 90 persisted evidence files
- `python3 artifacts/beaaa-15510/validate_rerun.py` - passed

## FAL Audit

- Focus: pass. The report reinforces that BackBond Scanner evidence is reliable because malformed judge output cannot silently become a pass or fail.
- Alignment: pass. No sentence claims insurance coverage, guarantee, rubric movement, scoring-math change, or customer-facing method change.
- Linkage: pass. Uses the common proof points of continuous scanner evidence, deterministic pins, N=5 reproducibility, and no promotion without QA approval.

## QA Gate

QA approval is not yet present. The candidate must be handed to QA Engineer `cd1e82a0-d1cf-4b44-a8ea-a8b312120baa` with the exact candidate SHA, acceptance criteria, and verification commands. No shared or production promotion is authorized until QA records `APPROVED` with evidence on BEAAA-15510 for that exact SHA.
