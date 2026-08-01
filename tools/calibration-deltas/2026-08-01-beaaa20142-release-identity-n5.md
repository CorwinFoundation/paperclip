# BEAAA-20142 — immutable release identity N=5 calibration delta

BLUF: The release-identity and publication-only candidate produces zero score or pillar movement; the locked golden suites passed five consecutive runs.

- `ticket_ref`: `BEAAA-20142`
- `classification`: rule execution — existing-operation release plumbing; no rubric, judge, probe, scoring, contract-output, or tamper-resistance behavior changed
- `contract_version`: `are-scanner-api-v1@v1.3.0`
- `scanner_version`: `0.5.0`
- `corpus_version`: `0.2.0`
- `probe-set-version`: `golden_agents_v1`
- `rubric_point_id`: `N/A — release identity and OCI publication only`
- `N=5 calibration delta`: total `0`; GOVERNANCE `0`; ROBUSTNESS `0`; BIAS_FAIRNESS `0`; OPS `0`; maximum spread `0`
- `cto_cosign_ref`: `N/A — rule execution`
- `command`: `python3 -m pytest -q tests/test_golden_agents.py tests/test_golden_set_l2_gate.py` repeated five times
- `results`: each run `19 passed, 18 subtests passed`; durations `1.31s`, `1.26s`, `1.25s`, `1.10s`, `1.35s`
- `score-path diff`: none; changed behavior is limited to build-time release-manifest rendering and the manual immutable GHCR workflow
- `FAL audit`: PASS — focus is exact release identity; alignment preserves deterministic Scanner evidence; linkage is the candidate commit, OCI revision, embedded manifest, and immutable repository digest
- `rollback`: revert the BEAAA-20142 candidate commit; no image was published, signed, installed, merged, or deployed by this task
