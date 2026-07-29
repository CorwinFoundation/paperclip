---
artifact_type: implementation_closeout
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-18957 gives the Passive Monitor deterministic, evidence-backed breach-chain semantics without changing the BackBond Score or existing covered-peril determinations."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-18957", "BEAAA-18947"]
proof_points_cited: ["tools/post-bind-ingest/src/post_bind_ingest/breach_chain.py", "tools/post-bind-ingest/tests/test_breach_chain_beaaa18957.py", "artifacts/beaaa-18947/two_sensor_breach_class_coverage_map_v0.1.md"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule execution under the additive reported-not-scored implementation lane; no score, rubric, judge, covered-peril, API-envelope, customer-data, or tamper-resistance change."]
---

# BEAAA-18957 implementation closeout

BLUF: The Passive Monitor now distinguishes sandbox escape, cross-principal credential reuse, and ordered lateral movement from generic unauthorized actions. The implementation is deterministic, reported-not-scored, rejects secret material, and leaves existing determination fixtures unchanged.

## Delivery and classification

This is an evidence-pipeline implementation for the CTO that adds breach-specific runtime observations within the eight-hour budget.

Classification: rule execution per `scanner-implementation-supervision-v1@v1.1`, classification row for implementation under existing rules. The module is additive and does not modify `determination/rules/determination_rules_v1.yaml`, the BackBond Scanner response envelope, a rubric point, score math, band, judge, covered peril, or tamper-resistance posture.

Implementation insight: generic unauthorized-action telemetry cannot prove an environment boundary crossing or a multi-hop chain. The new projector requires the identities and ordering facts needed for each breach class and binds every result to immutable manifest event references and canonical content hashes.

## Operational metadata

- `scanner_version`: `0.4.1`
- `corpus_version`: `2026.6.24`
- `probe-set-version`: `retell-consolidated-probe-set@2026-06-24`
- `rubric_point_id`: `OPS.MONITOR.POST_BIND` reported-not-scored evidence only
- `N=5 calibration delta`: exact zero. Six available locked golden fixtures replayed at seeds 0–4; total spread 0, total delta versus expected 0, and maximum pillar spread 0 for B1, G1, G2, M1, M2, and M3.
- `upstream_eval_version`: not applicable
- `cto_cosign_ref`: not required for rule execution
- Contract anchor: `are-scanner-api-v1@v1.3.0`; output shape unchanged

## Verification

- `python3 -m pytest -q tests/test_breach_chain_beaaa18957.py tests/test_evidence_chain_trace.py tests/test_no_score_imports.py`
- Result: `12 passed`
- `git diff --check`
- Result: pass

The focused cases cover positive, negative, and missing-field outcomes for all three semantics. They also prove deterministic replay, ordered immutable evidence refs, and fail-fast rejection of credential secret material.

## Rollback

Remove `breach_chain.py`, its package exports, and the focused test. Existing Passive Monitor normalization, generic peril detectors, determination rules, determination fixtures, and BackBond Score output remain unchanged.

## FAL and self-audit

- Focus: the implementation strengthens the continuous evidence half of the two-sensor underwriting loop.
- Alignment: it makes no claim of insurance coverage, remedy entitlement, or scored breach coverage.
- Linkage: it implements the explicit gaps recorded in the BEAAA-18947 two-sensor coverage map.
- Reproducibility: deterministic rules and canonical hashes; N=5 exact-zero score delta.
- Evidence completeness: every result carries manifest digest, source event ID, immutable artifact URI, event content hash, and result hash.
- Tamper resistance: evidence identifiers and canonical preimages are additive; secret material is rejected at the input boundary.
- Glossary and Carrier Rule: pass.
