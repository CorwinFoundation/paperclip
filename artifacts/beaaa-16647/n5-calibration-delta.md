---
artifact_type: n5_calibration_delta
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "The AIUC count freeze preserves BackBond Scanner score reproducibility by keeping reported-not-scored families outside score and count claims until live evidence exists."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-16647", "BEAAA-16264"]
proof_points_cited: ["tests/test_beaaa16647_aiuc_count_freeze.py", "tests/test_aiuc_path_b_lane_c_beaaa16272.py"]
version: "0.2"
prior_version: "artifacts/beaaa-16647/n5-calibration-delta.md@330ccebe40064cdbb7e5efa337fea186c81c4375"
self_audit_passed: true
escalations_noted: ["No rule-change escalation: no BackBond Score math, pillar weights, bands, judge model, or API output shape changed."]
---

# BEAAA-16647 N=5 Calibration Delta

BLUF: total and pillar deltas are exactly zero by construction. The R3 remediation corrects the immutable 49-row roster to the actual d407401 source facts (53 headings, E007/E014 retired, 51 non-retired, two non-retired headings tracked but excluded from the frozen 49 active contract) while preserving the active-test registry count predicates from v0.1. It does not import AIUC Path-B families into `rubric.scoring`, `rubric.bands`, admittance, judge selection, or BackBond Score aggregation.

```json
{
  "scanner_version_baseline": "0.4.1",
  "scanner_version_candidate": "0.5.0",
  "corpus_version": "family-corpora@0.1.0",
  "probe_set_version": "aiuc-path-b@0.1.0",
  "requirement_roster_version": "0.2",
  "upstream_heading_count": 53,
  "upstream_retired_count": 2,
  "upstream_non_retired_count": 51,
  "active_requirement_count": 49,
  "additional_non_retired_exclusions": 2,
  "seeds": [0, 1, 2, 3, 4],
  "total_delta": 0,
  "pillar_deltas": {
    "GOVERNANCE": 0,
    "ROBUSTNESS": 0,
    "BIAS_FAIRNESS": 0,
    "OPS": 0
  },
  "rubric_point_id": "AIUC Path-B reported-not-scored families; no scored rubric_point_id activated",
  "cto_cosign_ref": null
}
```

Verification:

- `python3 -m pytest tests/test_beaaa16647_aiuc_count_freeze.py tests/test_active_test_registry_beaaa13644.py tests/test_distinct_case_count_beaaa14423.py tests/test_beaaa16270_aiuc_path_b_lane_a.py tests/test_beaaa16271_aiuc_path_b_lane_b.py tests/test_aiuc_path_b_lane_c_beaaa16272.py` -> `98 passed`.
- `PYTHONPATH=. python3 tools/active_test_registry.py --json-out artifacts/beaaa-16647/active-test-counts.json --registry-out artifacts/beaaa-16647/active-test-registry.json` -> `published_active_test_count=1`, `active_executable_count=1`, `target_bound_runtime_count=203`; emits the known BEAAA-14423 distinct-pair warning.
- `python3 -m pytest` -> collection failed before executing tests because unrelated tool suites cannot import their expected local modules in this checkout (`are_connector`, post-bind ingest `app`, duplicate module basenames).
