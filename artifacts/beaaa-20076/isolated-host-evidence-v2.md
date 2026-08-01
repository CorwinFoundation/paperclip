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

# Isolated host activation evidence v2

BLUF: The non-production harness passed 20 tests covering the complete activation and rollback state machine requested by QA; no shared or production system was contacted.

Command: `pytest -q tests/test_scanner_deploy_beaaa16598.py`

Result: `20 passed in 7.96s`.

The harness substitutes isolated Docker, systemd, curl, environment, state, unit, and archive paths. It observes exact image load/tag calls, service cutover and rollback restarts, local and public gates, the unit's read-only state mount, fsynced receipts, and restored filesystem state. Negative cases cover archive bytes, OCI revision, manifest SHA, repository digest, candidate id, duplicate environment keys, modes, ownership, caller-selected archive paths, stale HTTP 200 rollback identity, ABSENT markers, present markers, rollback failure, `SIGINT`, and `SIGTERM`.

FAL audit: PASS. The evidence focuses on exact atomic activation, aligns with the unchanged Scanner contract, and links to BEAAA-20072, BEAAA-20077, and BEAAA-19984.
