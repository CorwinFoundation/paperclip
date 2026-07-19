---
artifact_type: calibration_delta
owner: scanner_engineer
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-14430 repairs CI surface and transport error semantics without changing scoring, corpus, probes, rubric points, or output-envelope contracts."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-14430"]
version: "v0.1"
self_audit_passed: true
scanner_version: "0.5.0"
corpus_version: "unchanged"
probe_set_version: "unchanged"
rubric_point_id: "all existing points; no scoring-path change"
n5_calibration_delta: "PASS - exact zero on every live golden fixture and pillar"
cto_cosign_ref: "pending exact-head review"
---

# BEAAA-14430 CI gate semantics N=5 receipt

BLUF: The recovered CI gate fix is score-neutral. The canonical N=5 gate
passed with byte-identical output and exact-zero drift on every live golden
fixture and pillar.

## Classification and scope

- Classification: CI integration and transport error handling.
- Baseline: canonical `origin/master@ca43d1306bbcecaec2c5033acc0c68949616007d`.
- Changed seams: `ci_plugin.gate`, `ci_plugin.runner`, `ci_plugin.client`, CI
  surface documentation, and focused regression tests.
- No rubric, weight, corpus, probe mapping, judge, applicability, model pin,
  scanner API contract, or stored score changed.
- Rollback: pure revert; no migration or stored-result rewrite.

## N=5 exact-zero delta

`python -X utf8 scripts/beaaa6190_v05_rc_n5.py` returned `PASS`. The
canonical gate re-scored every live fixture five times and the generated
receipt remained byte-identical to the locked repository artifact.

| Golden fixtures | Runs per fixture | Total spread | Max pillar spread | Locked-output identity |
|---|---:|---:|---:|---|
| B1, G1, G2, M1, M2, M3 | 5 | 0 | 0.0000 | true |

## Focused and protected verification

- BEAAA-11729 + BEAAA-14430 focused tests: `24 passed`.
- CI plugin, CI scan extension, edge-gate, and bias-pack gate slice:
  `85 passed, 1 deprecation warning`.
- GitLab template permits only exit `75` as neutral.
- GitHub maps only canonical infra exit `75` to a workflow warning and process
  success; the action output retains `exit_code=75` and `advisory=true`.
- Missing threshold is `EX_CONFIG` (`78`) and breaks both CI surfaces.
- Malformed JSON and invalid UTF-8 200 responses are classified as
  `ScanTransportError(kind="unreachable")`, so retry exhaustion resolves to
  the canonical infra advisory without leaking response bodies.
