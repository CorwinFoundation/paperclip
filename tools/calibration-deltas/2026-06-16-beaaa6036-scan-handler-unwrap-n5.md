---
artifact_type: other
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "This receipt preserves BackBond Scanner reproducibility evidence for a handler-only error-mapping fix."
contracts_referenced: ["are-scanner-api-v1", "scanner-implementation-supervision-v1"]
ticket_refs: ["BEAAA-6036", "BEAAA-6032"]
proof_points_cited: ["tests/test_scan_handlers_beaaa547.py", "tests.test_golden_agents", "tests.test_golden_set_l2_gate"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: []
---

# BEAAA-6036 N=5 Calibration Delta

BLUF: PASS. BEAAA-6036 changes only `api/scan_handlers.py` error translation for wrapped `OrchestratorError` failures. It does not touch rubric weights, scoring math, judge models, probe sets, corpus fixtures, or tamper-resistance posture.

## Classification

Rule-execution under `contracts/scanner-implementation-supervision-v1.md@v1.1`, table row "Smoke test for an existing operation" plus "Refactor that doesn't touch behavior." The change surfaces already-contracted API error codes for existing scanner failures; it does not change `contracts/are-scanner-api-v1.md@v1.0` output shape or field meaning.

## Required Fields

| Field | Value |
|---|---|
| `scanner_version` | `0.4.2` |
| `corpus_version` | unchanged; golden fixture replay uses fixture-pinned corpus versions |
| `probe-set-version` | unchanged; no probe-set files modified |
| `rubric_point_id` | OPS eval reproducibility / API error handling; no scoring rubric point modified |
| `N=5 calibration delta` | 0 total movement and 0 subscore movement by construction; explicit N=5 golden regression replay passed 5/5 |
| `cto_cosign_ref` | not required; rule-execution |
| `upstream_eval_version` | n/a; no upstream eval wrapper added or bumped |

## Verification

Targeted handler regression:

```text
pytest -q tests/test_scan_handlers_beaaa547.py
35 passed in 0.47s
```

Golden regression:

```text
python3 -m unittest -v tests.test_golden_agents tests.test_golden_set_l2_gate
Ran 19 tests in 0.994s
OK
```

Explicit N=5 replay:

```text
for i in 1 2 3 4 5; do python3 -m unittest -q tests.test_golden_agents tests.test_golden_set_l2_gate; done
N=1 OK
N=2 OK
N=3 OK
N=4 OK
N=5 OK
```

## FAL Audit

Pass. Focus is preserved because the artifact protects BackBond Scanner evidence handling for customer-facing API failures; alignment holds because this is not a standalone evaluation product; linkage uses the shared proof points `scanner_version`, `corpus_version`, `probe-set-version`, `rubric_point_id`, and `N=5 calibration delta`.
