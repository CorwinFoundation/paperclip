---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-14013 converts BackBond Scanner Appendix A inventory into evidenced executed cases under the stricter audit correction gate."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-14013", "BEAAA-13915", "BEAAA-13990", "BEAAA-13992"]
proof_points_cited: ["artifacts/beaaa-14013/execution_ledger.json", "artifacts/beaaa-14013/canonical-registry-2026-07-13.json", "tools/canonical_test_registry.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
scanner_version: "1.2.0"
corpus_version: "scanner-test-registry-baseline-2026-07-13"
probe_set_version: "retell-consolidated-probe-set@2026-06-24"
rubric_point_id: "P1.1,P1.2,P1.5,P2.5"
n5_calibration_delta: "not_applicable_execution_manifest_only_no_score_path_change"
cto_cosign_ref: null
---

# BEAAA-14013 Closeout

BLUF: executed 305 Appendix A cases under the BEAAA-13915 correction gate by adding target-bound evidence manifests for three existing T1 in-repo runner families. No BackBond Scanner scoring math, rubric weights, judge model pins, tamper-resistance posture, calibration corpus, or `are-scanner-api-v1@v1.0` output shape changed.

## Classification

- Deliverable: execution-evidence batch for CTO / BEAAA-13915 parent, causing 305 Appendix A candidates to satisfy the seven execution-evidence conditions within one Scanner Engineer heartbeat.
- Supervision classification: rule-execution per `scanner-implementation-supervision-v1@v1.1`, table row "Implement a new probe inside an existing rubric_point_id" / existing eval-family execution under current rules.
- Contract anchor: `are-scanner-api-v1@v1.0`.
- N=5 calibration delta: not applicable; execution manifest only, no score path change.
- `cto_cosign_ref`: not required.

## Count Result

| Metric | Value |
| --- | ---: |
| executed_count_before | 1 |
| added_counted_cases | 305 |
| executed_count_after | 306 |
| remaining_gap_to_1180 | 874 |
| target-call count | 305 |
| judge-call count | 0 |
| pass | 305 |
| fail | 0 |
| partial | 0 |
| unresolved | 0 |

## Families Executed

| Family | Cases | Runner | Evidence SHA-256 |
| --- | ---: | --- | --- |
| `hallucination_legal` | 125 | `harness/bench/hallucination_legal.py` | `a87c9f5327cf362e8c660c00d31934f022153c9ddda0ab9dbdc9359b7110a47a` |
| `confidence_calibration` | 100 | `harness/bench/confidence_calibration.py` | `f7f5cbf389d419f4778f2a2668e7cc699f8613aca2e9f750b07b8fa82d652666` |
| `citation_integrity_legal` | 80 | `harness/bench/citation_integrity_legal.py` | `ea3db10b90826f698be1d4613e3d87335ce9cf5b3cd19acbee84b4bce7d69c2f` |

Batch ledger SHA-256: `46ed13d82444490cb8f839129b8cb697187c42482a36a90e49c060e51d55cdc2`.

Canonical registry SHA-256 after correction: `306e81575b9663e4e828af5574388d36e3c47dd3bbc2d8f41b6bc2d717513c89`.

## Files Touched

- `scripts/beaaa14013_execute_appendix_cases.py`
- `IFA/scanner-test-registry-baseline-2026-07-13.csv`
- `tests/test_canonical_test_registry_beaaa13762.py`
- `artifacts/beaaa-14013/hallucination_legal.execution_evidence.jsonl`
- `artifacts/beaaa-14013/confidence_calibration.execution_evidence.jsonl`
- `artifacts/beaaa-14013/citation_integrity_legal.execution_evidence.jsonl`
- `artifacts/beaaa-14013/execution_ledger.json`
- `artifacts/beaaa-14013/canonical-registry-2026-07-13.json`
- `artifacts/beaaa-14013/active-test-counts-2026-07-13.json`
- `artifacts/beaaa-14013/active-test-registry-2026-07-13.json`

## Verification

- `python3 scripts/beaaa14013_execute_appendix_cases.py` passed and generated 305 evidence rows.
- `PYTHONPATH=. python3 -m tools.canonical_test_registry --repo . --emit artifacts/beaaa-14013/canonical-registry-2026-07-13.json --check` passed.
- `python3 tools/active_test_registry.py --check --json-out artifacts/beaaa-14013/active-test-counts-2026-07-13.json --registry-out artifacts/beaaa-14013/active-test-registry-2026-07-13.json` passed.
- Focused pytest run passed: `tests/test_canonical_test_registry_beaaa13762.py tests/test_framework_worker_registry_beaaa13682.py`.

## FAL Audit

Internal-only artifact; Brand Backbone gate not required. Canonical naming clean. Carrier Rule not implicated. No customer, carrier, premium, or external coverage claim introduced.

## Rollback

Remove the three `execution_evidence_file` references from `IFA/scanner-test-registry-baseline-2026-07-13.csv`, delete `artifacts/beaaa-14013/`, revert `scripts/beaaa14013_execute_appendix_cases.py`, and restore the pre-BEAAA-14013 test assertions. The executed count returns to 1 and the remaining gap returns to 1,179. No score-moving rollback is required.
