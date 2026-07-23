---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-17220 preserves BackBond Scanner evidence completeness by making Wave 2 target-bound records durable before any fail-closed stop can freeze a candidate."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-17220", "BEAAA-17196", "BEAAA-17218", "BEAAA-17091"]
proof_points_cited: ["pytest -q tests/test_aiuc_lane_a_depth_beaaa16535.py -> 32 passed in 191.48s", "retention_format_version wave2-case-seed-retention/v0.1"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Classified rule-execution under scanner-implementation-supervision-v1 row 'Refactor that does not touch behavior' / bug fix under existing fail-closed evidence-retention semantics; no rubric, scoring, judge, corpus, API, endpoint, model, pricing, or tamper-resistance reduction. No external-bound asset, so Brand Backbone gate not applicable."]
---

# BEAAA-17220 Implementation Closeout

BLUF: Wave 2 target-bound evidence retention is now crash-safe for completed case-seed records. Each completed record is atomically written, fsynced, directory-fsynced, hash-sealed, and recoverable before a non-PASS verdict can trigger a fail-closed stop.

## Scope

- Scanner version: `0.5.0`
- Corpus versions: `out_of_scope_containment@0.2.0`, `harmful_output_golden@0.2.0`, `ai_disclosure@0.2.0`
- Probe-set-version: `aiuc-wave2-lane-a@0.2.0`; per-family `@0.2.0`
- Rubric point IDs: `P1.4`, `OPS.SCOPE.OUT_OF_SCOPE_CONTAINMENT`, `OPS.REFUSAL.HARMFUL_OUTPUT_GOLDEN`, `P4.1`, `GOVERNANCE.TRANSPARENCY.AI_DISCLOSURE`
- Contract: `are-scanner-api-v1@v1.3.0`
- N=5 calibration delta: total `0`, pillars `0`, verified in `tests/test_aiuc_lane_a_depth_beaaa16535.py::test_reported_not_scored_n5_delta_is_exact_zero`
- CTO co-sign ref: not required for this rule-execution bug fix

## Implementation

- Added `RETENTION_FORMAT_VERSION = "wave2-case-seed-retention/v0.1"`.
- Added `DurableCaseRecordStore`: writes one JSON record per `(family, case_id, seed)` through temp-file write, file fsync, atomic rename, and directory fsync.
- Added reader validation: rejects corrupt JSON, retention-format mismatch, record-hash mismatch, and duplicate case-seed keys.
- Moved record construction before `stop_after_non_pass`; fail-closed non-PASS raising happens only after the completed record is durable.
- Bound candidate manifest and promotion identity to `durable_case_record_set_sha256` when a durable store is used.

## Test Evidence

Command:

```bash
pytest -q tests/test_aiuc_lane_a_depth_beaaa16535.py
```

Result: `32 passed in 191.48s`.

Targeted acceptance coverage:

- Injected deterministic non-PASS at record 36 (`ai_disclosure`, `AD-12`, seed `2`) and raised abrupt fail-closed stop immediately after the stop decision.
- Fresh reader recovered exactly 36 unique case-seed records, including record 36.
- Record 36 includes prompt, raw response, independently derived decision/verdict, request/response hashes, model id, seed, timestamps, family/corpus/config/policy/evaluator hashes, and no credential values.
- Manifest preimage and promotion identity reference the same durable record-set hash.
- Resume duplicate protection fails closed on repeated case-seed execution.
- Partial/corrupt `.json` record files are detected and rejected.

## Artifact Hashes

- `evals/aiuc_path_b/lane_a_depth.py`: `sha256:d4dba1ffcc69782df7c3cd61db1b10b90ab5d8e54910d3ea4f487711390e0ff5`
- `tests/test_aiuc_lane_a_depth_beaaa16535.py`: `sha256:11c7cf5d3ca612e5869e4e59dae77f5d47566ca4eb7754ba8594400a38046ad2`

## Rollback

Discard the BEAAA-17220 successor commit only. That removes the durable Wave 2 retention wrapper and tests while preserving Wave 1A and all prior Wave 2 reported-not-scored evidence unchanged. No merge, deploy, activation, signing, underwriting use, live call, or Founder route is authorized by this artifact.
