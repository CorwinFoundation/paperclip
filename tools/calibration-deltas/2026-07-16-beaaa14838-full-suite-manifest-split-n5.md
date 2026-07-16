---
artifact_type: calibration_delta
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-14838 splits full-suite manifest responsibilities behind a compatibility facade with byte-identical fixed-manifest evidence and exact-zero frozen-roster score drift."
contracts_referenced: ["are-scanner-api-v1@v1.9.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-14838", "BEAAA-14836", "BEAAA-14832"]
proof_points_cited: ["tests/test_full_suite_manifest_beaaa13645.py", "artifacts/beaaa-387/manifest.json", "rubric/rubric.yaml"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule-execution refactor; no rubric, scoring, judge, contract, manifest-schema, or tamper-resistance change. No CTO co-sign required because prior-vs-new delta is exactly zero."]
scanner_version: "0.5.0"
corpus_version: "artifacts/beaaa-387 frozen golden fixture corpus"
corpus_hash: "sha256:3561a38e73746731734b4daa011a28663c4edc9e333ae88b138657b232db5e8d"
probe_set_version: "full-suite-execution-manifest/v0.1"
rubric_point_id: "all frozen golden roster points; infrastructure-only refactor"
n5_calibration_delta: "PASS: 8/9 scored roster, seeds 0..4, prior-vs-new median delta 0.0000 for total and every pillar; candidate spreads 0.0000"
cto_cosign_ref: null
---

# BEAAA-14838 full-suite manifest split N=5 receipt

**BLUF:** PASS. The extraction is score-neutral and preserves the fixed signed manifest exactly. Eight scored frozen fixtures were replayed at seeds 0, 1, 2, 3, and 4; the candidate spread is zero for total and every pillar. B2 remains the approved non-scored roster exclusion.

## Pins

- `scanner_version`: `0.5.0`
- runtime contract literal: unchanged; documented contract anchor `are-scanner-api-v1@v1.9.0`
- corpus: `artifacts/beaaa-387/manifest.json`, SHA-256 `3561a38e73746731734b4daa011a28663c4edc9e333ae88b138657b232db5e8d`
- rubric: `1.3.0`, SHA-256 `9518937e1d3edcc9ca494de1f437ba57434f670b586b6ce59f0fec724ec1210a`
- probe-set-version: `full-suite-execution-manifest/v0.1`
- seeds: `0, 1, 2, 3, 4`
- model versions: fixture-pinned `model_versions` from each frozen baseline; no model or LLM judge invoked by the manifest split
- fixed signed-manifest SHA-256: `312e7112bfcb73e329423adc02d1af899e3a95ace8a578a1e56fba15c0890258`
- fixed unsigned-payload signature input SHA-256: `3d7f5c38fa358d0537e07b7bd2914c1a43a41f401aa061abded8cbd23b4abc01`

## Exact-zero delta table

| Agent | Median Δ total | Median Δ OPS | Median Δ BIAS | Median Δ ROB | Median Δ GOV | Candidate total spread | Max pillar spread |
|---|---:|---:|---:|---:|---:|---:|---:|
| B1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| B2 | excluded | excluded | excluded | excluded | excluded | excluded | excluded |
| B3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| G1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| G2 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| G3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| M1 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| M2 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| M3 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

Prior and candidate scoring inputs are identical because the extracted modules are outside `rubric.scoring`; the test execution repeats the frozen inputs on both sides of that invariant. The fixed-manifest test separately pins canonical payload bytes, payload hash, signature input, and persisted bytes.

## Verification and rollback

- Focused manifest and executor suites prove compatibility imports and behavior.
- `test_beaaa14838_facade_canonical_bytes_hash_signature_and_persistence` proves canonical bytes equal persisted bytes and freezes both hashes.
- Rollback: revert the extracted `api/full_suite_manifest_*` modules and restore the facade implementation. No data or schema migration is involved.
