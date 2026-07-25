# BEAAA-17733 implementation evidence

BLUF: Ordinals 33 and 34 now have configured-target runners that preserve multi-turn session identity, independently evaluate raw target output, fail closed to `INVALID`, and emit content-addressed evidence without activating scoring.

- Deliverable: probe implementation for the CTO that closes two canonical execution gaps within 10 agent-hours, 60 target calls, and USD 10.
- Classification: rule-execution under `scanner-implementation-supervision-v1@v1.1`, table row "Implement a new probe inside an existing rubric_point_id."
- Contract: `are-scanner-api-v1@v1.0`; output shape and scoring are unchanged.
- Scanner version: `0.5.0`.
- Corpus version: `canonical-61-family-registry@2026-07-13`.
- Probe-set version: `p3_5_multiturn_jailbreak@0.1.0,p3_6_output_attack@0.1.0`.
- Rubric points: `P3.5,P3.6`.
- CTO co-sign: N/A, rule-execution.

## Verification

- Focused regressions after malformed-response remediation: `29 passed in 4.96s`.
- Real HTTP boundary regressions cover JSON list, string, null, and invalid JSON bodies; every case terminates `INVALID / malformed_target_response` with a retained content-addressed evidence file.
- Repository `tests/` suite: `2862 passed, 13 skipped, 17 failed, 18 errors` in 316.03s. The failures/errors are pre-existing baseline/environment conditions outside this two-family diff, including missing `artifacts/beaaa-14662/production_bound_case_bindings_v1.jsonl`, stale active-registry count expectations, and unrelated production-control fixtures. The top-level suite also cannot collect `tools/are-connector/tests` because `are_connector` is not installed and encounters duplicate `test_no_score_imports` module names.
- Bounded smoke: local test-only configured target; fresh session namespace `beaaa-17733-fresh`; 10 stateful cases x 4 calls plus 20 output cases x 1 call; `60/60` calls; `PASS/PASS`; USD `0.00/10.00`.
- Credentials/data: test-only environment reference; no network, customer data, production credentials, or paid calls.
- Evidence: `artifacts/beaaa-17733/smoke/*.json`, including config, corpus, input, output, transcript, verdict, and record hashes.
- N=5: seeds 0-4; total and every pillar delta `0.0`; see `n5-calibration.json`.
- Rollback: remove the registry runner paths and specialized runner; rows remain `executable_not_connected` until exact-SHA QA approval.
- FAL: internal implementation evidence reinforces continuous Scanner evidence, does not alter the commercial promise, and links to the pinned BackBond Scanner versions.
