---
ticket_ref: BEAAA-20076
artifact_type: test_evidence
persona_audience: internal
scanner_contract: are-scanner-api-v1@v1.3.0
scanner_version: 0.5.0
corpus_version: 0.1.0
probe-set-version: unchanged
rubric_point_id: N/A-host-deployment-plumbing
N=5_calibration_delta: 0
---

# Simulated activation harness evidence v2

BLUF: The simulated subprocess harness passed 20 logic tests; it does not satisfy the separate real-host systemd/Docker gate, and no shared or production system was contacted.

Command: `pytest -q tests/test_scanner_deploy_beaaa16598.py`

Result: `20 passed in 7.96s`.

The harness substitutes Docker, systemd, curl, environment, state, unit, and archive paths. It asserts image load/tag calls, service cutover and rollback calls, local and public gate logic, unit mount arguments, fsynced receipts, and restored filesystem state. Negative cases cover archive bytes, OCI revision, manifest SHA, repository digest, candidate id, duplicate environment keys, modes, ownership, caller-selected archive paths, stale HTTP 200 rollback identity, ABSENT markers, present markers, rollback failure, `SIGINT`, and `SIGTERM`. These are simulated observations, not claims about real systemd or Docker execution.

FAL audit: PASS. The evidence focuses on exact atomic activation, aligns with the unchanged Scanner contract, and links to BEAAA-20072, BEAAA-20077, and BEAAA-19984.
