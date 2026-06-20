---
adr_id: ADR-0009
title: scan_log persistence invariants — write-once forensic_bundle_uri, JCS-canonical score_json storage, at-write-time snapshot_hash, append-only rows
status: accepted
author: CTO (88e68019-1cb6-4c4f-83e1-9fd340fb6821)
originating_ticket: BEAAA-1271
build_ticket: BEAAA-1271 (WS2 — persistence layer)
amended_by: BEAAA-5282 (§4.2 JCS storage format clarification)
co_signers:
  - CTO (88e68019) — producer authority
  - Underwriter (afa65747) — reproducibility surface co-sign (§4.2 amendment via BEAAA-5282)
scanner_version_when_drafted: pre-v0.3.0 (BEAAA-1271 WS2)
date: 2026-05-30
amended_date: 2026-06-14
audience: Scanner Engineer (implementer), Claims Architect (audit-trail consumer), Underwriter (reproducibility co-sign), external auditors
classification: rule-change (new persistence API surface; §4.2 amendment is rule-execution under same authority)
glossary: clean (ARE / IFA canonical; no BMI/ARI/BEAI in body)
rollback_tier: 2 (stop writing new rows; existing rows are append-only + write-once)
---

## 1. BLUF

`api/scan_log.py` provides a write-once, append-only SQLite persistence layer for scan results. Four invariants hold at all times:

1. **§4.1** `forensic_bundle_uri` is write-once per row (DB trigger + Python guard).
2. **§4.2** `score_json` is stored as the RFC 8785 JCS canonical serialization; `snapshot_hash` is `sha256(stored_bytes)`. External auditors verify integrity without a round-trip deserialize.
3. **§4.3** `snapshot_hash` is computed at write time and is never recomputed.
4. **§4** Rows are append-only; DELETE is blocked on any pinned row (DB trigger).

## 2. Decisions

### §4.1 — `forensic_bundle_uri` is write-once

The `forensic_bundle_uri` column is sealed on first write. A second `UPDATE` attempt against a non-NULL value raises `RAISE(ABORT, 'forensic_bundle_uri_write_once')` at the DB trigger layer, surfaced by the Python API as `ForensicBundleWriteOnce` and by the HTTP edge as 409 with reason `forensic_bundle_uri_write_once`.

The Python API (`finalize_pins`, `append_scan`) raises `ForensicBundleWriteOnce` eagerly before the DB layer so the API edge returns the canonical reason code regardless of which code path is exercised. The DB trigger is defense-in-depth.

### §4.2 — `score_json` storage format and `snapshot_hash` preimage

**Storage format (amended BEAAA-5282 2026-06-14):**

`score_json` is stored as `api.jcs.canonicalize(score_json).decode()` — the RFC 8785 JCS canonical UTF-8 string. This is identical to the bytes used to compute `snapshot_hash`.

**Why JCS rather than `json.dumps(sort_keys=True)`:**

`json.dumps(sort_keys=True)` and JCS produce different bytes for the same value. Python serializes integer-valued floats with a trailing `.0` (e.g. `1.0`); JCS §3.2.2.3 specifies ECMAScript 6 ToString which drops the fractional part (e.g. `1`). Also `json.dumps` uses space-separated colons (`: `) while JCS uses bare `:`. An external auditor computing `sha256(raw_db_bytes)` would get a different digest than `snapshot_hash` if the stored bytes were `json.dumps` output.

With the JCS storage format:

```
sha256(stored_score_json_bytes) == snapshot_hash
```

The verification path requires no round-trip: read `score_json` TEXT column, encode to UTF-8, SHA-256, prepend `sha256:`.

**`snapshot_hash` computation:**

```python
snapshot_hash = "sha256:" + hashlib.sha256(canonicalize(score_json)).hexdigest()
```

Equivalently:

```python
snapshot_hash = "sha256:" + hashlib.sha256(stored_score_json_bytes).hexdigest()
```

Both yield the same digest because the stored bytes are the JCS canonical form.

**Hash domain:**

The hash covers the full `score_json` envelope as passed to `insert_pending` / `append_scan`. The `forensic_bundle_uri` and `snapshot_hash` columns are NOT in the hash domain (they are persistence pointers and the hash itself).

**Implementation pin:**

`api/jcs.py::canonicalize()` at v1.2.0. Any future encoding change to `canonicalize()` is a contract bump; existing persisted hashes remain valid against their at-write-time bytes.

### §4.3 — `snapshot_hash` is at-write-time only

`snapshot_hash` is computed by the scanner immediately before the row is written and is stored once. It is never recomputed by a later read path.

**Tamper test (format-aware).** A row's `snapshot_hash` is authentic if it equals the recompute over the stored bytes under that row's storage format:

- **Amendment-era rows** (stored bytes are JCS canonical): authentic iff `sha256(stored_bytes) == snapshot_hash` (the direct path).
- **Legacy rows** (stored bytes are `json.dumps(sort_keys=True)`, written before `beaaa-5282-jcs-storage` merged): authentic iff `sha256(canonicalize(json.loads(stored_bytes))) == snapshot_hash` (the round-trip path documented in §5).

A row is treated as **tampered** — and MUST NOT be used as evidence — only when it satisfies **neither** path. A legacy row that fails the direct path but passes the round-trip path is authentic, not tampered. See §5 for the auditor disambiguation procedure for the heterogeneous table.

### §4 — Append-only rows

Pinned rows (rows where `forensic_bundle_uri IS NOT NULL OR snapshot_hash IS NOT NULL`) cannot be deleted. The DB trigger `trg_scan_log_append_only` enforces this. Un-pinned rows (crashed/aborted scans that never reached `finalize_pins`) may be garbage-collected.

## 3. Invariants — verification checklist

| Invariant | Mechanism | Test |
|---|---|---|
| `forensic_bundle_uri` write-once | DB trigger + Python guard | `test_write_once_forensic_bundle_uri_via_finalize_pins`, `test_write_once_forensic_bundle_uri_via_direct_db_update` |
| `snapshot_hash` write-once | DB trigger | `test_snapshot_hash_write_once_via_direct_db_update` |
| Append-only on pinned rows | DB trigger | `test_append_only_blocks_delete_on_pinned_rows` |
| Stored bytes == JCS canonical | Storage path uses `canonicalize().decode()` | `test_stored_bytes_are_jcs_preimage_beaaa5282` |
| `sha256(stored_bytes) == snapshot_hash` | Same preimage | `test_stored_bytes_are_jcs_preimage_beaaa5282` |
| Recompute over stored bytes is byte-for-byte stable (N=5) | Deterministic JCS | `test_n5_recompute_byte_for_byte_stable` |

## 4. Rejected alternatives

- **`json.dumps(sort_keys=True)` as storage format.** Rejected — produces bytes that differ from the JCS preimage for integer-valued floats and key separators. External auditors would need an undocumented round-trip (decode JSON, re-canonicalize via JCS) to verify `snapshot_hash`. Confusing and error-prone for claims-grade evidence verification.
- **Store raw bytes (BLOB column).** Rejected — TEXT column is self-documenting, portable across SQLite tooling, and matches the bundle content type (`application/json`). The JCS canonical form is valid JSON.
- **Recompute `snapshot_hash` on read.** Rejected — on-demand recomputation loses the ability to detect storage-side mutation. At-write-time hash is the tamper-evident seal (ADR-0017 §2.3 rationale).

## 5. Rollback

Tier 2. Stop writing new rows. Existing rows are append-only at the DB layer and cannot be deleted by application code. The `score_json` column format change (§4.2 amendment) affects only rows written after `beaaa-5282-jcs-storage` is merged; rows written before carry `json.dumps(sort_keys=True)` bytes. Those rows' `snapshot_hash` values were computed via `compute_snapshot_hash(json.loads(stored_bytes))` which is correct (the JCS of the deserialized dict), but external auditors must round-trip those rows. The amendment closes this gap for all new rows.

**Auditor disambiguation procedure (heterogeneous table).** The table is not backfilled — legacy and amendment-era rows coexist, and a row carries no explicit format-version column. An external auditor verifies any row deterministically without prior knowledge of its format:

1. Compute `direct = "sha256:" + sha256(stored_bytes).hexdigest()`. If `direct == snapshot_hash`, the row is authentic (amendment-era, direct path). Stop.
2. Else compute `roundtrip = "sha256:" + sha256(canonicalize(json.loads(stored_bytes))).hexdigest()`. If `roundtrip == snapshot_hash`, the row is authentic (legacy, round-trip path). Stop.
3. Else the row is tampered (§4.3) and MUST NOT be used as evidence.

Step 1 is the only path for rows written after this amendment; step 2 is the documented fallback for the finite, non-growing set of legacy rows. New rows never require step 2.

## 6. Co-sign record

| Principal | Role | Issue | Date |
|---|---|---|---|
| CTO (88e68019) | Producer authority — initial ADR at WS2 | BEAAA-1271 | 2026-05-30 |
| Underwriter (afa65747) | Reproducibility surface co-sign (ACCEPT) — §4.2 JCS storage amendment | BEAAA-5282 | 2026-06-17 |
| CTO (88e68019) | §4.2 amendment co-sign + §4.3/§5 tamper-rule reconciliation | BEAAA-5282 | 2026-06-20 |
