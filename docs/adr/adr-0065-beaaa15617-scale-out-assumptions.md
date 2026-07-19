---
artifact_type: adr
owner: cto
persona_audience: internal
phase: 1
canonical_terms_only: true
focus_claim: "BackBond Scanner scale-out becomes an explicit, evidence-gated decision while current single-node operation remains unchanged."
contracts_referenced: ["are-scanner-api-v1@v1.0", "scanner-implementation-supervision-v1@v1.0"]
ticket_refs: ["BEAAA-15617", "BEAAA-15604", "BEAAA-15629"]
proof_points_cited: ["api/scan_log.py", "api/scan_handlers.py", "api/edge_gateway.py", "api/sla.py", "api/full_scan_executor.py", "api/scan_runtime_factory.py"]
version: 1.0
prior_version: null
self_audit_passed: true
escalations_noted: ["Implementation is deferred. Any output-shape, scoring, rubric, corpus, band, judge, probe, or contract change requires its normal gate."]
---

# ADR-0065 - Inventory and gate BackBond Scanner scale-out assumptions

BLUF: The BackBond Scanner remains a single active application node until all five state surfaces below have durable, shared replacements and the listed verification gates pass. This ADR makes the current constraints explicit; it changes no runtime, score, rubric, corpus, band, or contract surface.

- Status: candidate, pending CTO record acceptance and QA approval of the exact candidate commit
- Classification: rule execution - documentation of existing architecture; no behavior or rule changes
- Contract: `contracts/are-scanner-api-v1.md@v1.0`
- Governing input: Founder directive [BEAAA-15604](/BEAAA/issues/BEAAA-15604), document revision `aecee8cb-a23c-4ed2-8ff3-585628140f93`, P4.1
- Priority constraint: implementation stays behind P1-P3 when owners contend

## Decision

Horizontal application-node scale-out is not authorized by this ADR. It is authorized later only through a single implementation proposal that assigns one shared-state design to every row in the inventory, preserves append-only evidence and idempotency, supplies rollback, and passes the verification gates below.

Until then, deployment must run one active writer/application node. Process restart may discard process-local state as described below; operators must not infer durability from an API object's lifetime.

## Single-node assumption inventory

| State surface | Evidence and current assumption | Scale-out failure | Implementation owner | Trigger to replace | Required rollback | Verification before cutover |
| --- | --- | --- | --- | --- | --- | --- |
| SQLite `scan_log` behind the connection lock | `api/scan_log.py` owns one SQLite connection; `api/scan_runtime_factory.py` binds the file path; ADR-0063 serializes shared-connection access with an application `RLock`. This protects threads in one process, not multiple processes or hosts. | Competing writers can create lock contention and operational split-brain; a node-local file also gives readers incomplete history. | CTO assigns Scanner platform engineering; Database Reliability reviews migration and recovery. | Before a second active writer, multi-process worker pool, or node-local filesystem deployment. | Stop new writers, drain leases, restore the last verified SQLite snapshot, point the single node back to the pinned file, and reconcile post-snapshot evidence before reopening scans. | Append-only and write-once trigger parity; concurrent-writer test; ordered history parity; snapshot-hash parity; backup/restore rehearsal; N=5 score delta within the normal rule-execution band. |
| In-process scan request/result state | `api/scan_handlers.py` keeps compatibility state for `GET /scan/{scan_id}` with a 15-minute TTL; durable rescan history is separately owned by `scan_log`. | A follow-up request routed to another node can return not found; restart removes state before TTL expiry. | Scanner API owner. | Before more than one request-serving process or any availability objective requiring restart-safe readback. | Route all traffic to one drained node, disable the shared-state adapter, and restore the process-local TTL store. | Cross-node kickoff/readback test; TTL and deletion parity; restart test; customer and underwriting audience-shape contract tests; no scan result survives beyond the approved retention rule. |
| Rate limiter | `api/edge_gateway.py::RateLimiter` holds concurrency and time-window counters in process memory. | Each node enforces an independent allowance, multiplying tenant capacity and weakening concurrency limits. | API gateway/security owner. | Before a second gateway process or node receives tenant traffic. | Return routing to one gateway, disable the distributed limiter, and restore the last pinned per-key configuration. | Atomic cross-node acquisition/release; window-boundary tests; fail-closed dependency outage test; tenant-isolation test; configuration parity; load evidence at configured limits. |
| SLA registry | `api/app.py` creates one `SlaRegistry`; `api/sla.py` stores rolling samples in process-local deques. | Metrics fragment by node and reset on restart, making p50/p95/p99 and degradation evidence incomplete. | Scanner observability owner; Claims routing owner co-signs degradation evidence semantics. | Before multiple request-serving processes, or before SLA evidence is used as a production control or claims decision input. | Re-enable the local registry on one node and mark the distributed observation window incomplete until a full new window accrues. | Cross-node aggregation; deterministic rolling-window parity; restart continuity; late/duplicate sample handling; degradation-routing tests; dashboards explicitly expose incomplete windows. |
| Full-scan job store | `api/full_scan_executor.py::FullScanJobStore` documents an in-memory async store and keeps jobs, idempotency keys, leases, checkpoints, and manifests in dictionaries. | Restart loses queued/running jobs; nodes cannot share idempotency or leases; duplicate execution and conflicting finalization become possible. | Full-scan execution owner. | Before the async full-scan path is deployed across multiple workers, before restart-safe execution is claimed, or before it becomes the presented production scan. | Stop leasing, drain or cancel workers, export durable job records, return to one process, and requeue only jobs whose signed attempt manifests prove they are non-terminal. | Atomic idempotent submit; lease fencing and expiry; checkpoint/resume; exactly-one terminal finalization; crash/restart recovery; signed manifest/hash parity; duplicate-worker and partition tests. |

## Cross-cutting scale-out gate

The implementation proposal must pass all of these gates before traffic is distributed:

1. One authoritative shared-state system per surface, with explicit consistency, retention, backup, restore, and ownership rules.
2. Fenced leases or transactions wherever duplicate work could emit conflicting evidence.
3. Tenant isolation and fail-closed behavior under dependency loss.
4. Byte-identical contract envelopes and signed-manifest inputs relative to the single-node baseline.
5. A documented migration rehearsal and a documented rollback rehearsal using production-shaped data without production keys or signing material.
6. QA Engineer `APPROVED` evidence for the exact candidate commit/build before any shared-environment promotion, plus Founder sign-off for public or production promotion.

## Consequences

- The current deployment constraint is visible and testable: one active application writer/node.
- Adding replicas for availability or throughput is a coordinated state migration, not a scheduler-only configuration change.
- The five surfaces may use different storage technologies, but their cutover is evaluated as one end-to-end evidence path.
- This ADR does not select a database, cache, queue, or metrics backend. Technology selection belongs to the later implementation proposal and its operational evidence.

## Rollback of this decision record

If the inventory is wrong, revert this ADR and replace it with a corrected CTO-accepted revision before authorizing scale-out. No runtime rollback is required because this record changes no code or configuration. Preserve the superseded file and checksum until the replacement merges and checksum verification completes.

## Verification and calibration

- Named-assumption coverage: five of five Founder-directed surfaces are inventoried.
- Repository evidence: each row cites the current implementation module that holds the state.
- Contract check: no field, route, audience projection, score, band, or signing input changes.
- N=5 calibration delta: `0.0000` by construction because this documentation-only change does not execute in the scoring or harness path. The normal merge gate still applies to any later implementation.
- Harness invariant: not applicable; the N=5 harness is untouched.
- FAL audit: Focus passes by making evidence continuity explicit; Alignment passes because the ADR makes no product or protection claim; Linkage passes through the BackBond Scanner evidence spine and canonical contract reference.

## Operational metadata

- scanner_version: unchanged (`0.4.1` baseline)
- corpus_version: unchanged
- probe-set-version: unchanged
- rubric_point_id: none (architecture inventory only)
- N=5 calibration delta: `0.0000` by construction
- cto_cosign_ref: pending CTO record acceptance on BEAAA-15617

