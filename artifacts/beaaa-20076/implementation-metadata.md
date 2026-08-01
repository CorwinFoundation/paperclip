---
ticket_ref: BEAAA-20076
artifact_type: implementation_evidence
persona_audience: internal
classification: rule-execution
classification_row: host deployment plumbing under existing Scanner rules
scanner_version: 0.5.0
corpus_version: 0.1.0
probe-set-version: unchanged
rubric_point_id: N/A-host-deployment-plumbing
N=5_calibration_delta: 0
cto_cosign_ref: N/A-rule-execution
scanner_contract: are-scanner-api-v1@v1.3.0
supervision_contract: scanner-implementation-supervision-v1@v1.1
source_decision: BEAAA-20072 v1.1 plus BEAAA-20079 v1.2 erratum
candidate_id: BEAAA-19984
status: qa_candidate
---

# BEAAA-20076 implementation evidence

BLUF: This host-plumbing candidate atomically binds the frozen BackBond Scanner image, environment, `current_version`, and candidate-specific release identity, and restores all four on failure without changing Scanner behavior or image bytes.

The systemd cutover is the single activation boundary, with root-owned identity state mounted read-only. Independent archive, OCI revision, manifest-file SHA, and repository-digest anchors fail closed before service mutation; local, Docker, public SLA, and public identity checks gate the durable receipt.

Verification: `pytest -q tests/test_scanner_deploy_beaaa16598.py` passed 7 tests. N=5 golden-agent delta is 0 by deployment-only scope invariant. No production installation, restart, registry pull, or external mutation was performed.

Rollback: one `ERR INT TERM` trap restores the prior environment, `current_version`, candidate identity, prior identity, image selection, and service. ABSENT sentinels restore nonexistence; failed recovery leaves a fsynced `ROLLBACK_FAILED` marker and transaction directory.

FAL audit: PASS. Focus is atomic activation; alignment preserves the frozen Scanner contract and image; linkage is BEAAA-20072, BEAAA-20079, and BEAAA-19984.
