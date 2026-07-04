# N=5 calibration delta — BEAAA-11726 CI/CD WS-4 scan-API extensions (implemented legs)

- **Ticket:** BEAAA-11726 (build for BEAAA-11714; ADR-0042)
- **Branch:** `beaaa-11726-ci-scan-ext` (base `origin/master` @ `07cd46c`)
- **Contract target:** `are-scanner-api-v1` v1.3.0 → v1.7.0 (DRAFT patch; CTO co-sign pending)
- **Scanner version:** unchanged this cut (no scanner binary emitted; new module `api/ci_scan_ext.py` is pure I/O)
- **Corpus version / probe-set version:** unchanged (no corpus or probe-set touch)
- **Rubric point ids affected:** none

## Result: delta = 0 on total and every subscore — **by construction**

The legs implemented on this branch (item 2 HMAC webhook signing, item 3 infra-vs-score error taxonomy, item 5 no-prompt `ci_plugin` adapter) are **pure I/O**. The change is **additive-only**:

```
?? api/ci_scan_ext.py
?? tests/test_ci_scan_ext_beaaa11726.py
```

No existing file is edited; the scoring function (`rubric/scoring.py`), the orchestrator (`scanners/live_probe_orchestrator.py`), the corpus, and the probe set are not entered. Therefore the golden-set N=5 spread is **identical** to the base commit — delta = 0 on total and every subscore holds by construction (ADR-0042 §"Reproducibility"; same precedent as the security-posture delta=0 rulings on BEAAA-5485 / BEAAA-10953).

## Verification run

- New suite: `tests/test_ci_scan_ext_beaaa11726.py` — **26 passed** (0.40s).
- Regression (unchanged behavior): `tests/test_scan_handlers_beaaa547.py` + `tests/test_scan_log_beaaa1271.py` — **78 passed** (4.44s).

## Pre-merge gate for the remaining legs

Item 1 (`scan_tier`) `full` tier golden N=5 must be re-run and attached showing delta = 0 vs base before merge; the `smoke` tier gets a separate deterministic ±1 repro assertion and is **never** compared against full-tier golden totals. Item 4 (`probe_set_hash` spine column) is audit-only → delta = 0 by construction. These land on the same branch under the single v1.7.0 CTO co-sign.
