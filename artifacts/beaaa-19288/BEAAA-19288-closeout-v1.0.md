---
artifact_type: scanner_implementation_closeout
owner: scanner-engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "All 596 frozen Vapi capability rows execute deterministically against isolated synthetic fixtures with zero network requests and no score."
contracts_referenced: ["contracts/are-scanner-api-v1.md@v1.3.0"]
ticket_refs: ["BEAAA-19217", "BEAAA-19220", "BEAAA-19285", "BEAAA-19288"]
scanner_version: "0.5.0"
corpus_version: "fa32e0e081ffbd2953ea3df8aa8d2d0faa0464a3076d17dad9b1758804488988"
probe-set-version: "1.4"
rubric_point_id: "none - non-score-bearing synthetic harness"
n5_calibration_delta: "artifacts/beaaa-19288/n5-calibration-delta.json"
cto_cosign_ref: null
self_audit_passed: true
---

# BEAAA-19288 closeout

BLUF: The assessment-local adapter dry-runs exactly 596 unique frozen cases, emits deterministic case-addressable traces, imports 453 prior request identities as a deny ledger, and contains no network or credential surface. It issues no BackBond Score.

## Verification

- `pytest -q tests/test_beaaa19288_vapi_synthetic_adapter.py` — PASS.
- Two independent seed-42 builder runs are byte-identical.
- Selection: 596 rows / 596 unique stable IDs; frozen source SHA `b32a1b7…fc008`.
- Capability: existing assistant executable `0`; adapter required `596`; 29 family counts sum to 596; every case has a reason.
- Dry run: 596 completed traces; concurrency 1; maximum 30 RPM-compatible; retries 0; network requests 0.
- Replay guard: 453 represented requests imported from the prior 380-row ledger; collision is STOP-before-execution.
- N=5: PASS, score not issued, total and pillar deltas 0.

## Evidence

- `selection-ledger.json` SHA-256 `0ab3e2627d2e1d852a368a903963238883ce1d09b69cd239ea9d8f2b4fdb30a7`
- `capability-analysis.json` SHA-256 `ebcba485e40d2785a1af60625858596cb5336d6c30d6a9b2ee7e43dcf302b137`
- `prior-request-deny-ledger.json` SHA-256 `03f40e65529f089665e90ef4ff71085e4038e61568391cc997bdc41771b60cca`
- `dry-run-ledger.json` SHA-256 `4ad02eefced1172324ea36ec905f07031c1a98c304718e1b689467b226948929`

FAL audit: PASS. The work focuses on evidence isolation, aligns with the frozen authorization boundary, and links the implementation to its parent chain. No external asset or brand gate applies.
