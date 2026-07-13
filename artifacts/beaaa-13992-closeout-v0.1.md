---
artifact_type: report
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-13992 restores BackBond Scanner count discipline by preventing unevidenced framework inventory from inflating executed test counts."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-13992", "BEAAA-13915"]
proof_points_cited: ["tools/active_test_registry.py", "tools/canonical_test_registry.py", "artifacts/beaaa-13992-active-test-counts-2026-07-13.json", "artifacts/beaaa-13992-canonical-registry-2026-07-13.json"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["BEAAA-13915 remains below the 1,180 target after correction; this is parent-goal scope, not a BEAAA-13992 blocker."]
scanner_version: "1.2.0"
corpus_version: "scanner-test-registry-baseline-2026-07-13"
probe_set_version: "scanner-test-registry-baseline-2026-07-13/v0.1"
rubric_point_id: "ALL_ACTIVE_DIMENSIONS"
n5_calibration_delta: "not_applicable_metadata_count_gate_only_no_score_path_change"
cto_cosign_ref: null
---

BLUF: BEAAA-13992 is complete. Framework and Appendix A cases now count as executed only when they are target-bound and carry execution evidence; unbound inventory remains visible but non-countable.

Classification: rule-execution per `scanner-implementation-supervision-v1@v1.1` rows "Refactor that doesn't touch behavior" and "Wrap an upstream eval framework ... as a BackBond Scanner probe" execution discipline. No rubric point, pillar weight, scoring math, judge model, tamper-resistance posture, or `are-scanner-api-v1@v1.0` output shape changed.

Change made:
- `tools/active_test_registry.py` now requires explicit execution evidence before a probe row can set `production_runner_executable=true`. Required evidence fields are concrete input/action, expected oracle, recorded target response, verdict, and GitHub provenance.
- `tools/canonical_test_registry.py` now requires an `execution_evidence_file` JSONL manifest before Appendix A embedded cases can be promoted. Each manifest row must carry the existing BEAAA-13990 executed-test evidence fields.
- `tools/framework_worker_registry.py` now reports worker active rows only for rows that are executable under the stricter active registry gate.
- `tools/active_test_registry_claims.json` was reconciled from 102 to 1 executed active case under the corrected gate.

Corrected count result:
- Active executable count: 1.
- Appendix A promoted embedded cases: 0.
- Appendix A disqualified embedded cases: 743.
- Remaining gap to 1,180: 1,179.

Verification:
- `python3 tools/active_test_registry.py --check --json-out artifacts/beaaa-13992-active-test-counts-2026-07-13.json --registry-out artifacts/beaaa-13992-active-test-registry-2026-07-13.json` PASS.
- `PYTHONPATH=. python3 -m tools.canonical_test_registry --repo . --emit artifacts/beaaa-13992-canonical-registry-2026-07-13.json --check` PASS.
- `python3 -m pytest tests/test_canonical_test_registry_beaaa13762.py tests/test_framework_worker_registry_beaaa13682.py -q` PASS: 15 passed in 98.41s.

N=5: not applicable for this metadata/count-gate correction. No score path, rubric weight, judge, calibration corpus, model version, seed handling, or API score envelope changed.

Rollback plan: revert this ticket's edits to the three registry tools, `tools/active_test_registry_claims.json`, the focused test update, and the three BEAAA-13992 generated artifacts. The previous inflated count posture returns, so rollback should only be used if the board explicitly rejects the target-bound evidence rule.

FAL audit: internal-only artifact; Brand Backbone gate not required. Canonical naming clean. Carrier Rule not implicated. No external claims or customer/carrier names introduced.
