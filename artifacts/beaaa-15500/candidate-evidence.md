---
artifact_type: scanner_bug_fix_evidence
issue: BEAAA-15500
persona_audience: internal
scanner_version: "0.5.0"
corpus_version: "0.1.1"
probe-set-version: "7ac1e54df3e5a46b77b2aca83f906cbb1244e1b6"
rubric_point_id: "N/A — customer projection only"
n5_calibration_delta: "0 on every golden agent; scoring paths are byte-identical to parent 3fa862f"
cto_cosign_ref: null
contract: "contracts/are-scanner-api-v1.md@v1.0"
classification: rule-execution
---

# BEAAA-15500 candidate evidence

BLUF: The fix-forward centralizes customer-visible admittance on the permitted projection, removes raw score values from failure narratives and CI events, and adds recursive HTTP response/event regression coverage for happy, bind-reject, and CI-regression scenarios. It does not push, deploy, promote, change scoring, or alter the Scanner API contract.

## Verification

- Regression proof on rejected merge `3fa862ffad35b9a86477280928ed5b61fe70eaea`: `python3 -m pytest -q tools/post-bind-ingest/tests/test_demo_app.py -k failure_scenario_http_responses_fail_closed` → `2 failed`; both failures exposed raw admittance keys.
- Fixed focused path: `python3 -m pytest -q tools/post-bind-ingest/tests/test_demo_app.py -k 'failure_scenario_http_responses_fail_closed or job_streams_agent_passive_tests_and_cicd'` → passed.
- Full demo suite: `python3 -m pytest -q tools/post-bind-ingest/tests` → 32 passed.
- Formatting: `git diff --check` → passed.
- N=5 action delta: zero. The candidate changes only `tools/post-bind-ingest/demo/` and its tests; all Scanner scoring, rubric, corpus, probe, judge, and golden inputs remain byte-identical to parent `3fa862f`. Parent ancestry carries the QA-approved `7ac1e54` N=5 evidence.

## Rollback

Revert the single BEAAA-15500 child commit. This restores `3fa862f` exactly; do not deploy that reverted state because it contains the reproduced customer-surface leak.

## FAL audit

Internal engineering evidence only. Claims are bounded to recorded commands and customer-projection behavior; no customer, carrier, coverage, pricing, or promotional claim appears.
