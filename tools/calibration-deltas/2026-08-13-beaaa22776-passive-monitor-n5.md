---
artifact_type: test_report
owner: scanner_engineer
persona_audience: internal
canonical_terms_only: true
focus_claim: "BEAAA-22776 changes only the reported Passive Monitor surface and produces zero BackBond Score or pillar delta across seeds 0..4."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-22776", "BEAAA-5864", "BEAAA-5966"]
version: 1.0
self_audit_passed: true
escalations_noted: []
scanner_version: "0.5.0"
corpus_version: "0.1.0"
probe-set-version: "post-bind-detectors-v0.1"
rubric_point_id: "OPS.MONITOR.POST_BIND"
n5_calibration_delta: 0
cto_cosign_ref: null
---

# BEAAA-22776 N=5 calibration delta

BLUF: seeds 0, 1, 2, 3 and 4 each pass the local Passive Monitor acceptance suite and the no-Scanner-score-import guard. Total and all pillar deltas are zero because the candidate does not import or mutate Scanner scoring, rubric, corpus, judge, or API output code.

- Classification: rule-execution under `scanner-implementation-supervision-v1@v1.1`, eval tooling and existing contract implementation.
- Contract anchor: `are-scanner-api-v1@v1.0`.
- Command: `PYTHONHASHSEED=<seed> PYTHONPATH=tools/post-bind-ingest/src pytest -q tools/post-bind-ingest/tests/test_beaaa22776_passive_monitor.py tools/post-bind-ingest/tests/test_no_score_imports.py`.
- Result per seed: 10 passed.
- Score delta per seed: total 0; GOVERNANCE 0; ROBUSTNESS 0; BIAS_FAIRNESS 0; OPS 0.
- False-positive fixture threshold: zero; observed zero on the clean-control fixture.
- FAL audit: focus is one Passive Monitor implementation slice; authority is the existing runtime-evidence lane; linkage is BEAAA-22776 → BEAAA-22773. No external claim or protection statement is introduced.

The detector is pinned as `backbond-local-rules@1.0.0`, has no network or third-party runtime dependency, inspects raw span content ephemerally, scrubs PII, and retains only peril identifiers plus a SHA-256 evidence digest. The ASI view is additive to the canonical Pn identifier and is absent from all scoring paths.
