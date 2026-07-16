---
artifact_type: calibration_delta
owner: scanner_engineer_2
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-14921 consolidates one approved implementation each for F5, F7, F8, and F9 without changing scoring, corpus, probe mappings, rubric points, or output-envelope semantics."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-14921", "BEAAA-14426", "BEAAA-14428", "BEAAA-14429", "BEAAA-14455"]
version: "v0.1"
self_audit_passed: true
scanner_version: "0.5.0"
corpus_version: "sha256:c9e1109cdf7ce46c035f432fd6b8597f902baa4b5d1975b677ec520eab6e1628"
probe-set-version: "unchanged"
rubric_point_id: "all existing points; no scoring-path change"
n5_calibration_delta: "PASS - exact zero on every baselined golden fixture and pillar"
cto_cosign_ref: "pending consolidated diff review"
---

# BEAAA-14921 consolidated N=5 calibration receipt

BLUF: The refreshed F5/F7/F8/F9 consolidation is score-neutral. The golden N=5 gate passed with exact-zero drift on every baselined fixture and pillar.

## Classification and scope

- Classification: rule-execution consolidation. F5 hardens request intake while preserving private vendor-VPC targets; F7 serializes shared ScanLog connection access; F8 adds credential-safe background-worker error logging; F9 corrects latent RFC 8785 / ECMA-262 float serialization.
- Baseline: canonical `origin/master@1e2408e752295003ca8b56f37b2afb0793bee368`.
- Scanner contract: `are-scanner-api-v1@v1.3.0`; output shape and documented endpoint semantics are unchanged.
- Seeds: `0, 1, 2, 3, 4`.
- No rubric, weight, corpus, probe mapping, judge, applicability, or model pin changed.

## N=5 exact-zero delta

`python3 -m pytest tests/test_beaaa398_wiring.py -q` returned `7 passed`. The gate re-scores the locked eval outputs five times and asserts zero drift for all baselined fixtures.

| Golden fixture | Seeds | Total delta | GOV | ROB | BIAS | OPS |
|---|---:|---:|---:|---:|---:|---:|
| Every fixture exercised by the canonical gate | 0-4 | 0 | 0 | 0 | 0 | 0 |

## Focused verification

`python3 -m pytest tests/test_scan_log_concurrency.py tests/test_jcs_encode_float_beaaa14429.py tests/test_scan_handlers_ssrf_beaaa14455.py tests/test_scan_handlers_beaaa547.py -q` returned `81 passed, 23 subtests passed`.

- F5: loopback/link-local/metadata literals reject; RFC1918 vendor-VPC target remains accepted; shared egress predicate is reused.
- F7: concurrent shared-connection writes complete without errors or dropped rows.
- F8: exactly one ERROR record contains scan id and exception type, never credential-bearing exception text.
- F9: 1,018-value Node oracle, affected exponent bands, large magnitudes, and golden-envelope hash identity pass.

## Refresh notes and rollback

The current master extracted request validation from `api.scan_handlers` into `api.scan_request_validation`. F5 was ported into that owned seam and re-exported through the compatibility facade used by existing tests. The colliding F5 ADR number was advanced from ADR-0059 to ADR-0060; F7 retains ADR-0059 and current master already owns ADR-0061.

Rollback is a revert of the consolidated commit. No data migration, stored score rewrite, production deployment, or release-gate change is included.
