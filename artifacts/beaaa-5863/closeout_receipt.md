---
artifact_type: closeout_receipt
owner: scanner_engineer
co_authors: ["cto", "scanner_engineer_se2"]
persona_audience: internal
phase: 2
canonical_terms_only: true
focus_claim: "BEAAA-5863 ships the foundational post-bind ingest gateway + normalizer (`tools/post-bind-ingest/`) bound to Log Event Schema v1.0 @ canon `e19cfb9` and ADR-0034 @ canon `8d61761`, with full coverage by the 10 required test modules (61 tests, all pass), N=5 byte-identical determinism on the golden-event fixture (spread=0), and Δ=0 on `are-scanner-api-v1@v1.0` Output by construction (enforced by the Auditor import-edge check `test_no_score_imports.py`)."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.1", "log-event-schema-v1@v1.0"]
ticket_refs: ["BEAAA-5863", "BEAAA-5812", "BEAAA-5815", "BEAAA-5816", "BEAAA-5817", "BEAAA-5818", "BEAAA-5819", "BEAAA-5864", "BEAAA-5865", "BEAAA-5866", "BEAAA-5867", "BEAAA-5740", "BEAAA-5752", "BEAAA-5754", "BEAAA-5757", "BEAAA-5761"]
proof_points_cited:
  - "Log Event Schema v1.0 byte-locked publish — `contracts/log-event-schema-v1.md` @ `e19cfb9` (BEAAA-5816)"
  - "ADR-0034 Phase 2.0 post-bind log architecture — `docs/adr/adr-0034-beaaa5815-phase-2-post-bind-log-architecture.md` @ `8d61761` (BEAAA-5815)"
  - "CTO design brief — comment `0538c336-…` on BEAAA-5863 (2026-06-16T14:48:34Z)"
  - "are-connector secret-family substrate — `tools/are-connector/src/are_connector/redaction.py` (BEAAA-5740 / referenced by BEAAA-5816 §Status header A2 symbol fix)"
  - "BEAAA-5761 `_build_peril_rollup` defense-in-depth pattern — `tools/portal_v1/portal.py` (out-of-canon; pattern lifted at the shape level into `post_bind_ingest.score_invariants`)"
non_score_moving: true
contract_output_shape_unchanged: true
classification: rule-execution (ADR-0034 §7 rows (a) + (c))
supervision_contract: scanner-implementation-supervision-v1@v1.1
scanner_version: not touched (post-bind ingest is outside the scoring surface)
corpus_version: not touched
probe_set_version: not touched
rubric_point_id: not applicable
n5_calibration_delta: N/A by construction — receipt at `tools/calibration-deltas/2026-06-16-beaaa5863-post-bind-ingest-vs-master.md`
n5_determinism_self_audit: spread=0 across 5 runs on golden-event fixture (sha256:5f7fb88db60eb5c79c121f8840b2f68b1dae18d3b7a0ba6ee915d79563aa4147 on every run)
cto_cosign_ref: pending — field-by-field schema conformance review on PR
se2_cosign_ref: pending — fresh-eyes review on manifest_signature.py, invariants.py, redaction.py floor enforcement
self_audit_passed: true
budget_used: ~1 dev-day (inside the 1.5–2 dev-day guidance, under the 3 dev-day hard ceiling)
escalations_noted:
  - "BEAAA-5864 (taxonomy reconciliation, P5 binding) is a soft-dep on detector children, NOT a blocker for 5863 — confirmed in CTO brief §2."
  - "Production S3 / KMS / IAM wiring is Phase 2.0.1 sub-child; MVP uses local-FS substrate + fixture-only HMAC keys per CTO brief §4."
  - "Pull-mode (Phase 2.5) remains a separate Founder gate."
---

# BEAAA-5863 closeout — Phase 2.0 build #1 post-bind ingest gateway + normalizer

## BLUF

`tools/post-bind-ingest/` lands as a foundational Phase 2.0 package: push-mode signed-manifest ingest gateway + raw_stdout adapter + normalizer producing typed events conforming to byte-locked Log Event Schema v1.0. The package is **outside** `are-scanner-api-v1@v1.0` Output by construction (Δ=0). All 10 required test modules pass (61 / 61). N=5 determinism on the golden-event fixture: spread=0. Ready for **CTO + SE-2 co-sign** on the branch `beaaa-5863-post-bind-ingest`.

## 1. Canon receipts

- Branch: `beaaa-5863-post-bind-ingest` (off `origin/master` @ `e19cfb9`).
- ADR-0034: `docs/adr/adr-0034-beaaa5815-phase-2-post-bind-log-architecture.md` @ `8d61761`.
- Log Event Schema v1.0 (byte-locked): `contracts/log-event-schema-v1.md` @ `e19cfb9`.
- N=5 determinism + Δ=0 receipt: `tools/calibration-deltas/2026-06-16-beaaa5863-post-bind-ingest-vs-master.md`.

Post-merge SHA: to be populated by CTO on co-sign + merge.

## 2. What landed

```
tools/post-bind-ingest/
  README.md                           — scope, module layout, Δ=0 attestation pointers
  pyproject.toml                      — package metadata, no third-party deps
  src/post_bind_ingest/
    __init__.py                       — SCHEMA_VERSION = "log-event-schema-v1.0"
    score_invariants.py               — BEAAA-5761 defense-in-depth block (importable, schema-pinned)
    ingest_gateway/
      __init__.py
      manifest_signature.py           — HMAC-SHA256 over canonical-JSON body
      manifest_schema.py              — envelope shape validation per ADR-0034 §2.1
      manifest_receiver.py            — orchestrator: schema → signature → idempotency → fetch
      idempotency.py                  — file-backed (customer_id, manifest_sha) dedup
      dead_letter.py                  — permanent-failure JSONL queue
    normalizer/
      __init__.py                     — normalize_stream: iter[raw_record] -> iter[v1 event]
      redaction.py                    — SECRET_PATTERNS floor (copy-and-adapt of BEAAA-5740) + 4 declared classes
      transcript_sha.py               — RFC 8785 JCS canonical-JSON + sha256:<hex> helper
      invariants.py                   — Schema §Cross-event invariants 1–5 enforcement
      adapters/
        __init__.py
        raw_stdout.py                 — MVP adapter (JSONL stdin); Datadog/Splunk/CW/OTEL are Phase 2.0.1
      event_families/
        __init__.py                   — FieldSpec + COMMON_ENVELOPE
        agent_invocation.py
        llm_completion.py
        tool_call.py
        tool_source_reference.py
        escalation.py
        error.py                      — module is `error`, imported as `error_family` at use site
        security_event.py
        pii_emission.py
        # claim_candidate_event intentionally absent — provenance is backbond_detector
  tests/
    conftest.py                       — sys.path setup
    _test_paths.py                    — shared REPO_ROOT / PACKAGE_ROOT / SRC
    test_manifest_signature.py        — 4 tests: round-trip, tamper, wrong key, unsupported alg
    test_manifest_schema.py           — 14 tests: required fields, UUID, sha256, byte_range, source_kind, schema_version
    test_idempotency.py               — 4 tests: first call, replay, persistence, gateway-level replay flag
    test_normalizer_determinism.py    — 3 tests: N=5 byte-identical, golden fixture covers 5 families, common envelope present
    test_redaction_floor.py           — 9 tests: AKIA / Bearer / JWT scrub, clean passthrough, clear-floor non-relaxation, drop, hash, redact-on-receipt, unknown class rejected
    test_redaction_drift_vs_connector.py  — 2 tests: byte-equality of SECRET_PATTERNS vs connector source (AST extraction); floor token = `[REDACTED:secret_family]`
    test_event_families_field_conformance.py  — 11 tests: parametrized across 8 families + common envelope + claim_candidate_event absence + README SHA pin
    test_cross_event_invariants.py    — 6 tests: invariants 1, 2, 3, 5 (4 is sink-time, not normalizer-time)
    test_no_score_imports.py          — 2 tests: AST walk over `src/post_bind_ingest/` + runtime sys.modules check after import
    test_score_invariants_published.py — 6 tests: schema pinned, published block all-true, pre/post clean, pre/post drift raises, None pair, one-sided None raises
    fixtures/
      golden_events/raw_records.jsonl — 5 raw records exercising 5 event families
      s3/                             — local-FS S3 substrate (populated by tests as needed)
      signing_keys/                   — fixture-only HMAC key seeds (populated by tests as needed)
```

Test summary: **61 tests, 61 pass, 0 fail, 0.37s**.

## 3. Acceptance criteria — issue body + CTO brief §5 sharpened

| AC | Source | Receipt |
|---|---|---|
| AC1 — `tools/post-bind-ingest/` lands on canon master | issue body | branch `beaaa-5863-post-bind-ingest` ready for merge after CTO + SE-2 co-sign |
| AC2 — receiver accepts signed S3 manifest, validates against schema, persists raw events | issue body | `ManifestReceiver.receive(...)` — see `test_idempotency.py::test_gateway_replay_marks_result_replayed` |
| AC3 — normalizer emits typed events conforming to byte-locked Log Event Schema v1.0 | issue body | `test_event_families_field_conformance.py` parses the Schema markdown @ `e19cfb9` and walks every field |
| AC4 — N=5 calibration delta self-audit shows Δ=0 by construction | issue body | calibration-deltas receipt + `test_no_score_imports.py` (AST + runtime); see §6 below |
| AC5 — defense-in-depth `score_invariants` block published | issue body | `post_bind_ingest.score_invariants` + `test_score_invariants_published.py` |
| AC6 — receipt: canon SHA + ADR-0034 cross-link + N=5 self-audit metadata in closeout | issue body | this document |
| Sharp AC1 — schema-version stability (Schema invariant 5) | CTO brief §5 | `test_manifest_schema.py::test_unsupported_schema_version_rejected` + `test_cross_event_invariants.py::test_invariant_5_mixed_schema_version_diagnostic` |
| Sharp AC2 — floor non-relaxation, byte-equality vs connector | CTO brief §5 | `test_redaction_drift_vs_connector.py` (AST-based byte-equality on SECRET_PATTERNS + flags) |
| Sharp AC3 — idempotency on (customer_id, manifest_sha) replay | CTO brief §5 | `test_idempotency.py` (4 tests) |
| Sharp AC4 — N=5 determinism on golden fixture | CTO brief §5 | `test_normalizer_determinism.py::test_n5_byte_identical` (in-package); standalone smoke captured in calibration-deltas receipt |
| Sharp AC5 — no score path touched | CTO brief §5 | `test_no_score_imports.py` (AST + runtime sys.modules) |
| Sharp AC6 — `score_invariants` defense-in-depth publish | CTO brief §5 | `test_score_invariants_published.py` |

## 4. N=5 determinism self-audit metadata

Procedure: run `normalize_stream` on the golden fixture 5 times with the same `NormalizationContext`; serialize canonical-JSON; SHA-256.

| Run | Event count | Output SHA-256 |
|---|---|---|
| 0–4 | 5 | sha256:5f7fb88db60eb5c79c121f8840b2f68b1dae18d3b7a0ba6ee915d79563aa4147 |

Spread = 0. Determinism conforms to Schema §Reproducibility.

## 5. Δ=0 attestation on `are-scanner-api-v1@v1.0` Output

By construction. The post-bind ingest package contains no scoring path:

- AST import-edge check: `tests/test_no_score_imports.py::test_no_module_imports_forbidden_roots` walks every `.py` under `src/post_bind_ingest/` and asserts no import from {`tools.portal_v1`, `tools.are_connector`, `scanner_v0_3_2`, `api.*`, `scanners.*`, `rubric.*`}.
- Runtime check: `tests/test_no_score_imports.py::test_runtime_import_does_not_load_forbidden_modules` confirms that importing the package transitively loads no forbidden module via `sys.modules`.
- Defense-in-depth: `post_bind_ingest.score_invariants.published_block()` returns the four-boolean BEAAA-5761 block all-`True`; detector children (BEAAA-5865/5866) inherit by re-using the same module.
- No file under `api/`, `scanners/`, `rubric/`, `pricing/` is touched on this branch.

## 6. Co-sign matrix (per CTO brief §6 / ADR-0034 §7 rows (a) + (c))

- **CTO** — field-by-field schema-conformance review on PR. Walk every event family module against Schema v1.0 §Event family tables (required / redaction / provenance triplets). The `test_event_families_field_conformance.py` test mechanizes the walk against the live Schema markdown at canon `e19cfb9`; CTO confirms semantics.
- **Scanner Engineer #2 (bc8396d5)** — cross-SE fresh-eyes review on:
  - `manifest_signature.py` (canonical-JSON + HMAC-SHA256, tamper-detection paths)
  - `invariants.py` (schema invariants 1–5)
  - `redaction.py` (floor non-relaxation, declared-class application order)
- **UW** — not required (no `claim_candidate_event` emission, no field feeds scoring); UW courtesy comment welcome.

SE-1 (this author) cannot self-cosign on field semantics per CTO brief §6.

## 7. Rollback

Single-revert: drop `tools/post-bind-ingest/` from canon master. No upstream module is touched (Auditor import-edge check enforces this). Schema v1.0 byte-locked stays unaffected.

## 8. Out of scope on this PR (per CTO brief §2)

- Detector logic (P4 / P5 / stubs) — BEAAA-5865 / 5866.
- Sink emitters (claims queue / rescore feed / calibration feed) + observed-vs-predicted appendix — BEAAA-5867.
- Production S3 / KMS / IAM wiring, per-customer credential rotation — Phase 2.0.1 sub-child.
- Pull-mode (Phase 2.5) — separate Founder gate.
- `claim_candidate_event` emission — provenance `backbond_detector`; normalizer never emits it (claim_candidate_event family is intentionally absent from `event_families/`).
- Pn taxonomy reconciliation — BEAAA-5864 (soft-dep on detector children, NOT on this ticket; normalizer never emits `claim_candidate_event`).

## 9. Glossary

Canonical terms only: BackBond, ARE Scanner (legacy term grandfathered in the contract filename `are-scanner-api-v1.md` per glossary), IFA (BackBond company), Log Event Schema v1.0. No BMI / ARI / BEAI / BEA in this document or in the code.
