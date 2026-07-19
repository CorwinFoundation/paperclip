# ADR-0063 — Serialize shared ScanLog connection access

BLUF: BEAAA-14428 adds one re-entrant application lock around every access to the process-wide SQLite connection, preventing concurrent request threads from interleaving transaction state and losing audit evidence.

- Status: candidate, pending CTO diff co-sign and QA approval
- Classification: rule execution — bug fix / reliability hardening under the existing audit-log contract
- Contract: `contracts/are-scanner-api-v1.md@v1.0`
- Supreme Law invariant: evidence completeness; no score, rubric, judge, probe, corpus, or output-shape behavior changes

## Decision

`ScanLog` owns a `threading.RLock`, and every public operation that touches its shared connection holds that lock for the complete operation. `finalize_pins` and `append_scan` keep their read-check and transaction within the same acquisition; nested `get` calls remain safe because the lock is re-entrant.

## Why

SQLite transaction state belongs to a connection. `BEGIN IMMEDIATE` coordinates separate connections through SQLite's file lock, but cannot prevent two Python threads sharing one connection from interleaving `BEGIN`, `ROLLBACK`, and `COMMIT`. The prior GIL-based comment overstated a guarantee that Python's SQLite calls do not provide.

Per-thread connections are rejected. Each `:memory:` connection has a separate database, which would fragment the default/test audit log; file-backed connections would also require busy-timeout policy and abandon the current single-writer invariant.

## Verification and calibration

The regression test drives 32 concurrent `append_scan` calls through one `ScanLog(":memory:")`, requires zero worker exceptions, and asserts all 32 rows persist. Existing scan-log and handler tests must remain green.

Golden calibration delta is 0 by construction: this change does not alter computed values or any scoring surface. The candidate still requires the ticket's CTO diff co-sign and the mandatory QA approval for the exact commit before merge or promotion.

## Rollback

Revert the candidate diff. No schema, data migration, API contract, or persisted output shape changes are introduced.

## Operational metadata

- scanner_version: unchanged (`0.4.1` baseline)
- corpus_version: unchanged
- probe-set-version: unchanged
- rubric_point_id: none (audit-log reliability only)
- N=5 calibration delta: 0 by construction; no score-moving surface touched
- cto_cosign_ref: pending on BEAAA-14428
- FAL audit: internal technical artifact; no unsupported external factual claim
