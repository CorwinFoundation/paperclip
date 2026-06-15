---
artifact_type: adr
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-5529 keeps the BackBond Scanner CI gate from reporting a false green when pytest-style acceptance tests are present."
contracts_referenced:
  - "are-scanner-api-v1@v1.0"
  - "scanner-implementation-supervision-v1@v1.1"
ticket_refs:
  - "BEAAA-5529"
  - "BEAAA-5527"
  - "BEAAA-5487"
proof_points_cited:
  - ".github/workflows/tests.yml"
  - "requirements-dev.txt"
  - "tests/conftest.py"
version: 0.1
prior_version: null
scanner_version: "n/a - CI discovery only"
corpus_version: "n/a - CI discovery only"
probe-set-version: "n/a - CI discovery only"
rubric_point_id: "n/a - CI discovery only"
n5_calibration_delta: "n/a - no Scanner scoring path, rubric point, corpus, probe, judge, or output shape changed"
cto_cosign_ref: "not_required_rule_execution"
self_audit_passed: true
escalations_noted: []
---

# ADR-0030 - BEAAA-5529 CI Pytest Discovery

**Decision:** switch the PR/push test workflow from `python -m unittest discover -v tests` to `python -m pytest -v tests`.

**Rationale:** pytest collects both legacy `unittest.TestCase` suites and pytest-style acceptance classes. The prior `unittest discover` command could report zero tests for Track B pytest-style integration coverage, creating a false-green CI gate.

**Classification:** rule-execution per `scanner-implementation-supervision-v1@v1.1`, row "Smoke test for an existing operation" / CI plumbing under existing rules. No rubric, scoring, judge model, corpus, tamper-resistance posture, or contract output shape changed.

**Rollback:** revert `.github/workflows/tests.yml`, `requirements.txt`, and `tests/conftest.py`. Rollback restores the known false-green risk for pytest-style Track B acceptance tests.
