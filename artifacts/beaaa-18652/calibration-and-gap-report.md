---
artifact_type: calibration_delta
owner: scanner_engineer
persona_audience: internal
canonical_terms_only: true
focus_claim: "Routes the retained Syllable transport through the current BackBond Scanner live-probe runner while preserving bounded public-showcase transport behavior."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-18652", "BEAAA-18609"]
scanner_version: "0.5.0"
corpus_version: "0.1.0"
probe-set-version: "inspect-section1-v0"
rubric_point_id: ["P1.1", "P2.1", "P3.1", "P4.1"]
N=5 calibration delta: "Syllable total spread 0; every pillar spread 0; golden gates 26 passed"
cto_cosign_ref: null
rule_classification: "rule-execution"
self_audit_passed: true
---

# BEAAA-18652 calibration and acceptance report

## BLUF

The retained `SyllableRunner` owns the two-step Syllable protocol used by the
hardened customer live-probe adapter. A focused integration test executes one
non-scoring smoke and five seeded current-route scans with total and every
pillar spread of 0. Signed evidence bundles are persisted and read back before
the scan log exposes their tenant-scoped `/forensic_bundle/{opaque_id}` paths.

## Verification

Exact command:

`python3 -m pytest -q tests/integration/test_beaaa18652_syllable_live_probe.py tests/integration/test_syllable_request_construction.py`

Result: `5 passed`. The focused route test uses Scanner 0.5.0,
corpus 0.1.0, seed 42, and `k_replicates=5`. It rejects `beaai.local` in the
emitted envelope, rejects every `.local` and speculative `s3://` reference,
proves correct-tenant retrieval, cross-tenant 404, restart persistence, signed
readback, credential redaction, prompt hashing, and email/phone redaction.

Regression command:

`env -u ARE_BUNDLE_SIGNING_KEY ARE_ALLOW_DEV_SIGNING_KEY=true python3 -m pytest -q tests/integration/test_beaaa18652_syllable_live_probe.py tests/integration/test_syllable_request_construction.py tests/test_bundle.py tests/test_app.py`

Result: `48 passed`.

Golden command:

`python3 -m pytest -q tests/test_golden_set_l2_gate.py tests/test_live_probe_variance_band_beaaa544.py`

Result: `26 passed`.

## FAL audit

- Focus: retained public showcase transport reaches the current Scanner path.
- Authority: implementation-only rule execution; no score, rubric, corpus,
  judge, contract shape, or promotion change.
- Language: internal engineering evidence; canonical product terminology used.
