---
adr_id: ADR-0059
title: Serialize ScanLog connection access behind an app-level RLock (concurrent /scan corrupts the audit spine)
status: accepted
classification: bug-fix / reliability hardening — no rubric, probe set, judge, scoring math, corpus, or API-contract surface touched → Δ=0 on goldens by construction → no Underwriter pricing gate, no CEO rubric gate (CTO ruling on BEAAA-14408)
authored_by: Scanner Engineer (d8bbc3b5)
co_signers:
  - CTO (88e68019) — diff co-sign per contracts/scanner-implementation-supervision-v1.1 (PENDING)
date: 2026-07-15
ticket_refs:
  - BEAAA-14428 (this ticket — F7 impl)
  - BEAAA-14408 (parent — blocked on this; CTO classification ruling)
  - BEAAA-1273 (WS3 — the threadpool wiring that exposed the sharing)
  - ADR-0009 §4 (the durable evidence-spine invariants this protects)
contract_version: unchanged (are-scanner-api-v1@v1.3.0)
scanner_version: 0.4.1
glossary: clean (BackBond Scanner canonical; no BMI/ARI/BEAI/IFA)
rollback_tier: 0 (revert the diff — single file, additive lock; no schema, no data migration, no contract)
self_audit_passed: true
adr_number_note: 0059 is next-free against live master (highest present = ADR-0058). Reconfirm free at PR time and renumber if a sibling claimed it in-flight.
---

# ADR-0059 — Serialize ScanLog connection access behind an app-level RLock

## BLUF

`ScanLog` shares ONE `sqlite3.Connection` process-wide, and the app shares ONE
`ScanLog` across FastAPI's threadpool. Concurrent `/scan` handlers therefore run
`BEGIN IMMEDIATE ... COMMIT` on the **same connection from different threads**,
which raises `cannot start a transaction within a transaction` (and adjacent
`InterfaceError`/`ProgrammingError`) and **drops the ADR-0009 §4 audit row** — a
red-line evidence-trail gap. Fix: an app-level `threading.RLock` serializing
every access to the connection. One lock over the whole connection; correctness
over throughput. Δ=0 on goldens by construction.

## 1. Root cause (confirmed on canon origin/master @c1debd2; issue cited @76953a0 — `api/scan_log.py` byte-identical across both)

`api/scan_log.py` opens one connection for the process lifetime:

```python
self._conn = sqlite3.connect(self._path, isolation_level=None, check_same_thread=False)
```

`api/app.py` puts the `ScanRuntime` (which owns that single `ScanLog`) on
`app.state.scan_runtime` — one shared instance. FastAPI dispatches **sync**
`/scan` handlers on a threadpool, so concurrent scans call
`ScanRuntime.kickoff → ScanLog.append_scan` on the same connection, on different
threads.

`_transaction()` issues `BEGIN IMMEDIATE ... COMMIT`. `BEGIN IMMEDIATE`
serializes writers holding **separate** connections via the SQLite file lock. It
does **not** serialize two threads sharing ONE connection object: SQLite
transaction state is per-*connection*, not per-*thread*. When thread A is
mid-transaction and thread B issues `BEGIN IMMEDIATE` on the same connection,
SQLite raises `OperationalError: cannot start a transaction within a
transaction`; thread B's `ROLLBACK`/`COMMIT` in the except/else path can also
terminate thread A's transaction. The scan_log row then fails to write.

The prior in-code comment claimed "sqlite3's per-connection GIL coverage makes
single-connection cross-thread usage safe." That is **wrong**: CPython releases
the GIL around the sqlite3 C calls, so BEGIN/COMMIT interleave across threads.
The reproducing test (below) raises `cannot start a transaction within a
transaction` on canon (8 of 16 threads on the measured run; the exact count is
race-dependent, one or more raise every run).

## 2. Decision

1. Add `self._write_lock = threading.RLock()` in `ScanLog.__init__`.
2. Serialize **all** access to `self._conn` behind that lock — every public
   method: `get`, `list_for_agent`, `insert_pending`, `append_scan`,
   `finalize_pins`. For the two check-then-write methods (`finalize_pins`,
   `append_scan`) a **single** lock acquisition spans the pre-transaction
   `self.get()` read AND the `_transaction` write, so the NULL-pin precondition
   cannot race a concurrent finalize on the same key. `RLock` (reentrant) is
   required precisely because those methods call `self.get()`, which re-acquires
   the same lock.
3. Fix the misleading comment: state the real guarantee (app-level
   `threading.RLock` serializes all connection access); drop the GIL claim.

One lock over the whole connection is the correct posture: sqlite ops here are
sub-millisecond and the audit log is not a hot path — **correctness over
throughput**. The pure-compute `compute_snapshot_hash(score_json)` in
`append_scan` runs outside the lock (it touches no connection state), keeping
the critical section minimal.

## 3. Rejected: per-thread connections

Rejected because:

- The default/test path is `:memory:`, where **each connection is a SEPARATE
  in-memory DB** → the audit log would fragment/vanish across threads.
- For file-backed mode it still needs `busy_timeout` handling and breaks the
  single-writer invariant that ADR-0009 §4's write-once triggers rely on.

A single shared connection + one app-level lock preserves the single-writer
invariant with the least moving parts.

## 4. Reproducibility (Δ=0 by construction)

No computed value changes: no rubric point, pillar weight, judge model, probe
set, corpus, or scoring-math surface is touched. The change is purely the
serialization discipline around an existing storage layer. Δ=0 on the golden set
holds **by construction** — a full N=5 golden sweep is NOT required (standing
procedure governs score-moving changes only; this moves no score). The
concurrency test asserts the audit spine no longer drops rows; existing
`scan_log` + `scan_handlers` suites stay green (231 tests green locally across
the scan_log-adjacent suite set enumerated in §5).

## 5. Acceptance evidence

- New `tests/test_scan_log_concurrency.py`: N=16 threads, barrier-synchronized,
  each `append_scan` with a distinct `(agent_id, run_timestamp)` against one
  shared `ScanLog(":memory:")`. Asserts zero exceptions and all N rows
  persisted. **Fails on canon** (8/16 threads raised on the measured run; ≥1
  raises every run), **passes after the fix**; 5/5 repeat runs pass.
- Existing suites green: `test_scan_log_beaaa1271`, `test_scan_handlers_beaaa547`,
  `test_rescans_prior_scan_refs_beaaa1273`, `test_sla_beaaa1274`,
  `test_ci_scan_ext_beaaa11726`, `test_scan_completed_envelope_beaaa13169`,
  `test_rescans_handler_beaaa1265`, `test_log_tracker_beaaa6159`.

## 6. Rollback plan

Tier 0 — revert the single-file diff (`git revert <merge_sha>`). No schema
change, no data migration, no contract bump; reverting restores the prior
(buggy) behavior with zero cleanup.

## 7. Gate

CTO diff co-sign per `contracts/scanner-implementation-supervision-v1.1`. No
Underwriter gate, no CEO gate, no Founder gate (bug-fix reliability class;
tamper-resistance posture unchanged — this *strengthens* the durability of the
existing evidence spine rather than reducing any control).
