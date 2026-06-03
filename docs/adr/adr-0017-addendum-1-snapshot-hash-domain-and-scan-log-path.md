---
adr_id: ADR-0017-addendum-1
parent_adr: ADR-0017
title: Production runtime wiring for /scanner/v1/rescans — snapshot_hash domain + scan_log path + score_envelope_factory spec
status: accepted
classification: rule-execution under CTO design spec (no rubric/judge/probe-set touch)
authored_by: CTO (88e68019)
co_signers:
  - CTO (88e68019) — design + co-sign in this addendum
date: 2026-06-03
ticket_refs:
  - BEAAA-2132 (parent — Deploy /scanner/v1/rescans v1.2.0 to live endpoint)
  - BEAAA-2139 (child — Execute v1.2.0 cut-over; HALTED at runbook §3 pending this addendum)
  - BEAAA-1273 (WS3 — scan_log + score_envelope_factory wiring, in-tree)
  - BEAAA-1283 (WS7 — contract v1.2.0 ratification + Q7c hash domain pin)
contract_version: are-scanner-api-v1@v1.2.0
glossary: clean (ARE / IFA canonical; no BMI/ARI/BEAI in body)
rollback_tier: 2 (unchanged from ADR-0017 §6; Path 1 = ARE_RESCANS_MEASURED=off)
self_audit_passed: true
---

# ADR-0017-addendum-1 — Production runtime wiring for /scanner/v1/rescans

## 1. Why this addendum exists

ADR-0017 §2.3 + §7 Q7c pinned the **canonicalization** of `snapshot_hash` (RFC 8785 JCS; SHA-256; exclude `snapshot_hash` field itself). They did not pin (a) which envelope fields the hash spans, (b) where the `scan_log` relational store lives in prod, or (c) the wiring shape of `make_default_production_scan_runtime`. Scanner Engineer (BEAAA-2139, comment a0febecf) raised these on a cut-over §3 HALT. This addendum pins all three so the wiring extension is rule-execution under a CTO spec, not a rule-change escalation.

## 2. Decisions (binding)

### 2.1 Classification

The extension to `api/scan_runtime_factory.py::make_default_production_scan_runtime()` — passing non-None `scan_log` and `score_envelope_factory` to `ScanRuntime.__init__` — is **rule-execution** under the spec in §2.2–§2.4 below. CTO co-sign is provided by the act of publishing this addendum. No HoUW binding co-sign required (no rubric_point_id added; no probe-set/judge touch; no scoring math change).

If wiring deviates from this spec (e.g. envelope adds a field beyond §2.2; scan_log path differs from §2.4; canonicalization deviates from §7 Q7c), the deviation reverts to rule-change and re-escalates to CTO.

### 2.2 `score_envelope_factory` spec — what's in the snapshot_hash domain

Signature (unchanged from `api/scan_handlers.py:104`):

```
ScoreEnvelopeFactory = Callable[[ScanResult, str, str], dict[str, Any]]
```

Inputs: `(result: ScanResult, agent_id: str, run_timestamp: str)`.
Output: a dict containing **exactly** the deterministic fields below — no more, no less. The dict is canonicalized via `api/jcs.py::canonicalize()` and SHA-256 hashed to produce `snapshot_hash` per ADR-0017 §7 Q7c.

| Field | Source | Reason in-domain |
|---|---|---|
| `agent_id` | factory arg | identifies the scanned surface |
| `purpose` | `result.scanner_invocation_context.purpose` | rescan path branches on this; chain entries depend on it |
| `run_timestamp` | factory arg (UTC ISO-8601 from `run_timestamp_factory`) | deterministic ordering pin for prior_scan_refs[] |
| `scanner_version` | `result.scanner_version` | scoring code version pin |
| `corpus_version` | `result.corpus_version` | probe corpus pin |
| `rubric_version` | `result.rubric_version` | scoring rules pin |
| `seed` | `result.seed` | RNG pin (default = 42 per `api/app.py` BEAAA-21) |
| `model_versions` | `result.model_versions` (snapshot) | upstream model pin |
| `are_total` | `result.are_total` | scoring result |
| `band` | canonical letter (A/B/C/D), from `result.band.letter` | rubric band per BEAAA-57 FROZEN — NOT the legacy `bmi` key |
| `subscores` | `result.subscores` (4 pillars per rubric.yaml) | scoring result |
| `evidence_refs` | `result.evidence_refs[]` | OTel GenAI semconv per BEAAA-818 |
| `prior_scan_refs` | projected from `runtime.prior_scan_rows(agent_id, exclude_scan_id=current)` at envelope build via `_chain_entry_from_row` | ADR-0017 §2.1 immutability — entries pin at-write-time `are_total` + `band` |
| `partial_completion` | `result.partial_completion` | claims-grade SLA flag (BEAAA-1274 WS4) |
| `confidence_below_adjudication_threshold` | `result.confidence_below_adjudication_threshold` | claims-grade adjudication flag |

**Explicitly EXCLUDED from envelope (and therefore from `snapshot_hash`):**

| Field | Reason out-of-domain |
|---|---|
| `forensic_bundle_uri` | Persistence pointer; assigned at append_scan, written to the scan_log row as a separate column. URI immutability comes from `trg_scan_log_forensic_bundle_uri_write_once` (scan_log.py:60) + S3 Object Lock Compliance mode (ADR-0017 §7 Q7a) — not from the hash. Including it would be circular: the hash signs the bundle content; the URI points at the bundle. |
| `snapshot_hash` | Self-reference; ADR-0017 §7 Q7c explicit exclusion. |
| `purpose_aware_sla` block (BEAAA-1274 WS4) | Wall-clock runtime metric attached at API-edge AFTER kickoff (runtime layer in `api/app.py:172-173`). Lives in the **API response** but NOT in the **bundle**. Hash signs the bundle; the bundle is replayable from envelope-pin state alone. SLA metrics are observability and would break replay. |
| Infra/request metadata | `tenant_id` header, `request_id`, `process_id`, `instance_id`, IP, etc. — observability, not contract surface. |

**Replay invariant:** any party that fetches the bundle from `forensic_bundle_uri`, canonicalizes via `api/jcs.py::canonicalize()`, and SHA-256-hashes the result MUST get a byte-equal hex digest to the persisted `snapshot_hash`. This is the claims-grade verification path Claims Architect signed off on at BEAAA-1103.

### 2.3 Bundle = envelope

The forensic bundle written to S3 (URI: `s3://ifa-forensic-bundles/agent-{agent_id}/scan-{scan_id}.json`, Object Lock retain-until = `run_timestamp + 10y` per ADR-0017 §7 Q7a) is the canonical-JSON serialization of the envelope returned by `score_envelope_factory`. Bundle content == JCS(envelope). Hash is SHA-256(bundle content). The bundle does NOT include `forensic_bundle_uri`, `snapshot_hash`, or `purpose_aware_sla` for the same reasons §2.2 excludes them from the envelope.

The API response includes the bundle envelope + `forensic_bundle_uri` + `snapshot_hash` + `purpose_aware_sla`. Callers that want to verify recompute SHA-256(JCS(response \ {forensic_bundle_uri, snapshot_hash, purpose_aware_sla})) and compare against the response's `snapshot_hash`. Equivalently they fetch the bundle from S3, JCS-canonicalize it directly, SHA-256, and compare. Both paths yield the same digest.

### 2.4 `scan_log` persistence — path + durability assumption

**Phase 1 prod fleet (single Syllable tenant, single API instance):**
- Path: `/var/lib/are-scanner/scan_log.sqlite3`.
- Filesystem: EFS-mounted at `/var/lib/are-scanner` so multi-instance fleets (when we add them) read/write the same file. SQLite WAL mode for concurrent reader + single writer.
- Single-writer enforcement: only the API instance fronting `/scanner/v1/rescans` writes. Concurrent reads (e.g. `/scanner/v1/sla` snapshot path that doesn't touch scan_log directly) are allowed.
- Schema: `api/scan_log.py:42` (existing — append-only, write-once triggers on `forensic_bundle_uri` + `snapshot_hash`, RAISE(ABORT) on DELETE).
- Daily snapshot: cron-style backup writes `/var/lib/are-scanner/scan_log.sqlite3` to `s3://ifa-forensic-bundles/scan-log-snapshots/YYYY-MM-DD/scan_log.sqlite3` with S3 Object Lock Compliance mode + retain-until = snapshot_date + 10y (matches forensic bundle retention).
- Source of truth: forensic bundles in S3. The `scan_log` is the relational index. If the SQLite file is lost, the prior-scan chain is recoverable by listing `s3://ifa-forensic-bundles/agent-{agent_id}/` for any agent_id and re-canonicalizing each bundle to recompute `snapshot_hash`. The 10y Object Lock retention on bundles means claims-grade recovery is always possible from S3 alone.

**Out of scope here (Scope-γ candidates):**
- PostgreSQL migration. `scan_log` schema is portable (SQLite-syntax triggers translate to PG triggers); migration becomes worthwhile when concurrent writers (>1 API instance) or query analytics warrant it. Not needed for Phase 1 first-100-rescans.
- Multi-region replication. Forensic bundles are S3 single-region (us-east-1 implied by bucket name). Cross-region claims access goes through bucket replication; out of scope for this addendum.

### 2.5 `forensic_bundle_uri_factory`

The default at `api/scan_handlers.py:372` (`_default_forensic_bundle_uri`) already produces the spec URI per ADR-0017 §2.2 + §7 Q7a. **Do not override** in `make_default_production_scan_runtime` unless a real-bucket-side path/prefix differs. For Phase 1 the default is correct.

## 3. Acceptance gates (for BEAAA-2139 re-pre-flight)

After wiring per §2 lands, the pre-flight harness (`scripts/beaaa2139_cutover_preflight.py`) must show on a synthetic rescan against `make_default_production_scan_runtime()`:

| Gate | Pass condition |
|---|---|
| AG-1 | `scan_runtime._scan_log is not None` and points at the spec path |
| AG-2 | `scan_runtime._score_envelope_factory is not None` |
| AG-3 | Synthetic rescan response has non-null top-level `forensic_bundle_uri` matching `s3://ifa-forensic-bundles/agent-<agent_id>/scan-<scan_id>.json` |
| AG-4 | Synthetic rescan response has non-null `snapshot_hash` prefixed `sha256:` + 64-hex-chars |
| AG-5 | Recompute: SHA-256(canonicalize(response minus `{forensic_bundle_uri, snapshot_hash, purpose_aware_sla}`)) == response.snapshot_hash bytewise |
| AG-6 | scan_log row exists with `agent_id` + `run_timestamp` from envelope; `forensic_bundle_uri` + `snapshot_hash` columns non-null |
| AG-7 | N=5 calibration delta across B1/G1/G2/M1/M2/M3 = 0 spread (rule-execution invariant — no scoring change) |

On AG-1..AG-7 PASS, BEAAA-2139 resumes from runbook §3 step "First synthetic rescan" → §3 "Cut-over" → 24h soak.

## 4. Rollback

Unchanged from ADR-0017 §6.
- Path 1 (≤10 min): `ARE_RESCANS_MEASURED=off`. Reverts /scanner/v1/rescans to doc-only envelope.
- Path 2 (≤30 min): contract revert per §6.

Newly written `scan_log` rows on the failed cut would remain on disk; they are append-only + write-once and harmless under rollback. Forensic bundles in S3 are also harmless under rollback (write-once + Object Lock).

## 5. Hard invariants strengthened by this addendum

| Invariant | Mechanism |
|---|---|
| Bundle content is the hash domain | `score_envelope_factory` returns deterministic envelope; bundle = JCS(envelope); hash = SHA-256(bundle) |
| Replay is byte-deterministic | Envelope excludes wall-clock / observability fields; only pinned fields (scanner_version, corpus_version, rubric_version, seed, model_versions, etc.) feed the hash |
| URI immutability is separate from content immutability | `trg_scan_log_forensic_bundle_uri_write_once` + S3 Object Lock; hash signs content, not the URI string |
| scan_log loss is recoverable | Forensic bundles in S3 are source of truth; scan_log is relational index; daily SQLite snapshot to S3 narrows recovery window |
| No rubric/judge/probe change | Wiring extension does not touch `rubric/`, `scanners/`, `corpus/`, or any rubric_point_id |

## 6. Audit trail

This addendum, BEAAA-2132 plan document (`/BEAAA/issues/BEAAA-2132#document-plan`), BEAAA-2132 cut-over runbook (`docs/runbooks/scope-beta-v1.2.0-rescans-cutover.md` at git `131807f`), and the Scanner Engineer pre-flight artifacts (BEAAA-2139 comment a0febecf) together constitute the WS7 → cut-over evidence chain. UW operational pre-read at BEAAA-2132 24h-soak close reviews this chain.

## 7. Glossary self-audit

Body uses only ARE / IFA canonical terms. The legacy `bmi` key referenced in §2.2 + ScannerEngineer comment a0febecf is named only to call out its exclusion from the envelope (code-internal alias for the canonical `band.letter` field per `rubric/scoring.py:328`); it does not appear in any envelope output or response shape.

self_audit_passed: true
