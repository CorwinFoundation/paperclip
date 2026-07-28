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
N=5 calibration delta: "total spread 0; every pillar spread 0"
cto_cosign_ref: null
rule_classification: "rule-execution"
self_audit_passed: false
---

# BEAAA-18652 calibration and remaining gap

## BLUF

The retained `SyllableRunner` now owns the two-step Syllable protocol used by
the hardened customer live-probe adapter. A focused integration test executes
one non-scoring smoke and five seeded current-route scans with total and every
pillar spread of 0.

The issue is not shippable yet. The current customer walker emits
`https://evidence.are-scanner.local/...` and the scan log emits an `s3://`
forensic-bundle projection without uploading the referenced objects. Those
references fail BEAAA-18652's reachable-upload acceptance criterion. Replacing
them introduces or changes evidence persistence behavior and must be classified
by the CTO before implementation under the supervision contract.

## Verification

Exact command:

`python3 -m pytest -q tests/integration/test_beaaa18652_syllable_live_probe.py tests/integration/test_syllable_request_construction.py`

Result: `5 passed` in 15.84 seconds. The focused route test uses Scanner 0.5.0,
corpus 0.1.0, seed 42, and `k_replicates=5`. It rejects `beaai.local` in the
emitted envelope.

## FAL audit

- Focus: retained public showcase transport reaches the current Scanner path.
- Authority: implementation-only rule execution; no score, rubric, corpus,
  judge, contract shape, or promotion change.
- Language: internal engineering evidence; canonical product terminology used.
