---
ticket: BEAAA-5863
parent_ticket: BEAAA-5812
authority_adr: BEAAA-5815 ADR-0034 @ 8d61761
binding_contract: log-event-schema-v1.0 @ e19cfb9 (contracts/log-event-schema-v1.md)
supervision_contract: scanner-implementation-supervision-v1@v1.1
classification: rule-execution (ADR-0034 §7 rows (a) + (c))
non_score_moving: true
contract_output_shape_unchanged: true
scanner_version: pre-bind scoring path not touched
corpus_version: pre-bind scoring path not touched
probe_set_version: pre-bind scoring path not touched
rubric_point_id: not applicable — post-bind ingest is outside the scoring surface
n5_calibration_delta_status: N/A by construction
cto_cosign_ref: pending — requested on closeout comment
ralph_loop_pattern: false
self_audit_passed: true
---

# Calibration delta — BEAAA-5863 post-bind ingest vs master

## Why N=5 calibration delta is N/A on this PR

Per CTO standing procedure (and CTO brief §5 AC#4 + §8), the N=5 calibration delta on the golden agent set is **not applicable by construction** for this PR. The post-bind ingest package is **outside** `are-scanner-api-v1@v1.0` Output. No module under `tools/post-bind-ingest/src/post_bind_ingest/` imports any score-yielding code path. The import-edge check `tests/test_no_score_imports.py` enforces this and runs in CI.

The forbidden import roots are:

- `tools.portal_v1` — BEAAA-5761 rollup view + scoring entrypoints
- `tools.are_connector` / `tools.are-connector` / `are_connector` — BEAAA-5754 ProbeRuntime + redaction substrate (the SECRET_PATTERNS list is copy-and-adapt at the helper level; drift-guarded)
- `scanner_v0_3_2` / `src.scanner_v0_3_2` — BEAAA-2799 scanner core
- `api.*` — FastAPI / route handlers carrying score state
- `scanners.*` — pillar scanners
- `rubric.*` — rubric weights and dimension definitions

If a future contributor introduces an import from any of these roots into the post-bind ingest package, the import-edge check fails CI and this calibration-delta receipt is no longer N/A — the contributor is responsible for filing a fresh N=5 against the golden set and routing the change as rule-change per supervision-v1.1.

## Determinism self-audit (normalizer fixture, NOT the golden agent set)

Schema §Reproducibility requires the normalizer to be byte-deterministic across runs given the same input + the same `NormalizationContext`. This is a different attestation from the N=5 calibration delta on the golden agent set — it is the **shippable equivalent** for a non-score-moving change.

Fixture: `tools/post-bind-ingest/tests/fixtures/golden_events/raw_records.jsonl` (5 raw records exercising agent_invocation / llm_completion / tool_call / error / escalation).

Procedure: `normalize_stream` on the fixture, serialize canonical-JSON, hash.

| Run | Event count | Output SHA-256 |
|---|---|---|
| 0 | 5 | sha256:5f7fb88db60eb5c79c121f8840b2f68b1dae18d3b7a0ba6ee915d79563aa4147 |
| 1 | 5 | sha256:5f7fb88db60eb5c79c121f8840b2f68b1dae18d3b7a0ba6ee915d79563aa4147 |
| 2 | 5 | sha256:5f7fb88db60eb5c79c121f8840b2f68b1dae18d3b7a0ba6ee915d79563aa4147 |
| 3 | 5 | sha256:5f7fb88db60eb5c79c121f8840b2f68b1dae18d3b7a0ba6ee915d79563aa4147 |
| 4 | 5 | sha256:5f7fb88db60eb5c79c121f8840b2f68b1dae18d3b7a0ba6ee915d79563aa4147 |

**Spread:** 0 (single unique output SHA across 5 runs).
**Verdict:** byte-identical determinism. Conforms to Schema §Reproducibility.

Determinism guarantee mechanism (CTO brief §5 AC#4):
- No clocks: `t_received` is supplied by the gateway, frozen per-manifest; the normalizer never reads the wall clock.
- No RNG: event IDs are derived deterministically via UUIDv5 over the per-event `manifest_sha + index` seed.
- No live LLM: refusal_class is sourced from the customer payload (BEAAA-5757 substrate lift is helpers-only; runtime detector is BEAAA-5866 scope).
- Canonical-JSON serialization (RFC 8785 JCS).

## Δ=0 attestation on `are-scanner-api-v1@v1.0` Output

By construction. Proof:

- No score-yielding modules are imported by this package (`test_no_score_imports.py` — runtime + AST checks).
- No file under `api/`, `scanners/`, `rubric/`, `pricing/` is touched by this PR (`git diff --name-only origin/master..HEAD | grep -E '^(api|scanners|rubric|pricing)/'` returns no rows).
- Defense-in-depth: `post_bind_ingest.score_invariants.published_block()` returns `{are_total_unchanged, subscores_unchanged, band_unchanged, confidence_unchanged}` all `True` and is asserted by `test_score_invariants_published.py`.

## Receipt notes

- This receipt is filed under `tools/calibration-deltas/2026-06-16-beaaa5863-post-bind-ingest-vs-master.md`.
- Sibling JSON receipt is not required for this PR because no calibration delta was computed (would be misleading — implies a score-touching scope).
- The next time a Phase 2.0 child touches a score-yielding path (e.g. activation of P1 / P2 / P3 / P6 stubs into the rescore feed), a full N=5 calibration delta receipt against the golden agent set IS required, and supervision-v1.1 classification flips to rule-change.
