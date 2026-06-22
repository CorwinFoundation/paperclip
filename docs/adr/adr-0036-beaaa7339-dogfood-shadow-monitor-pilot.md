---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BEAAA-7339 proves the BackBond post-bind monitor can consume own-fleet traces in shadow mode without changing Scanner scoring or pricing surfaces."
contracts_referenced: ["are-scanner-api-v1@v1.3.0", "scanner-implementation-supervision-v1@v1.1"]
ticket_refs: ["BEAAA-7339", "BEAAA-7320"]
proof_points_cited: ["tools/post-bind-ingest/tests/test_dogfood_tap.py", "tools/post-bind-ingest/tests/test_sinks.py", "tools/post-bind-ingest/tests/test_no_score_imports.py"]
version: 0.1
prior_version: null
self_audit_passed: true
escalations_noted: ["Rule-execution classification: customer onboarding/data harness under existing schema and detectors; no rubric point, scoring math, judge model, tamper-resistance, or contract output-shape change."]
---

# ADR-0036: BEAAA-7339 Dogfood Shadow-Monitor Pilot

**Decision:** Add an isolated dogfood tap for own-fleet Codex and Claude traces, feeding the existing post-bind ingest normalizer and P5 detector in shadow/no-pay mode.

## Context

BEAAA-7339 requires a real-traffic monitor pilot before customer live-bind gates are open. Own-fleet traces are sufficient for a shadow test because they exercise the same trace classes the monitor expects: tool calls, LLM completions, and tool/source references. The pilot must not relax customer traffic gates, bind coverage, issue indemnity, or import Scanner scoring or pricing code.

## Decision

Implement `tools/post-bind-ingest/src/post_bind_ingest/dogfood_tap.py` as an additive adapter. The adapter:

- reads Codex rollout JSONL from `codex-home/sessions/YYYY/MM/DD/rollout-*.jsonl`;
- reads Claude JSONL transcripts from `.claude/projects/<workspace>/<session>.jsonl`;
- maps real trace shapes into `log-event-schema-v1.0` event types: `tool_call`, `llm_completion`, and `tool_source_reference`;
- applies the existing field-level redaction helper at capture time before manifest retention;
- emits a SHA-256 signed manifest compatible with `normalizer.normalize_manifest()`.

`claims_queue` now carries explicit `mode`, `dogfood`, `auto_pay`, `bind`, and `indemnity` fields. Dogfood calls set `mode: "shadow"` and `dogfood: true`; money movement and binding fields remain false.

## Classification

Rule execution under `scanner-implementation-supervision-v1@v1.1`, row: "Customer onboarding harness for a new customer archetype" / "Smoke test for an existing operation." No new rubric point, judge model, scoring math, pillar weight, or contract output shape is introduced.

## Invariants

- Shadow only: no pay, no bind, no indemnity.
- Redaction before retention: retained manifest payloads contain redacted fields plus hashes, not raw prompts/results.
- Firewall preserved: post-bind dogfood tap and sinks do not import rubric, scoring, scanner, portal, or pricing modules.
- Contract anchor: `are-scanner-api-v1@v1.3.0`; no Scanner API response shape is changed.

## Rollback

Delete `dogfood_tap.py`, remove the claims queue mode fields, and drop the BEAAA-7339 tests. No migration or state cleanup is required.
