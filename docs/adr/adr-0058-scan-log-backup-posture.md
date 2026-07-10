---
adr_id: ADR-0058
title: Recurring integrity-checked backup posture for scan_log.sqlite3 (srv1749248)
status: accepted
classification: rule-execution / CTO-unilateral ops tooling — no rubric, probe set, judge, scoring math, or contract touch → no Underwriter gate
authored_by: CTO (88e68019)
co_signers:
  - CTO (88e68019) — design + accept
date: 2026-07-10
ticket_refs:
  - BEAAA-13386 (this ticket)
  - BEAAA-12895 (parent — criterion 6 backup/snapshot posture)
  - BEAAA-13169 (customer scan envelope — restore health check references it)
contract_version: unchanged (are-scanner-api-v1@v1.3.0)
glossary: clean (ARE/BackBond canonical; no BMI/ARI/BEAI)
rollback_tier: 0 (additive ops units; disable = `systemctl disable --now` both timers, remove backup root; live scoring path untouched)
self_audit_passed: true
adr_number_note: renumbered from provisional 0059 → 0058 (next free against live master; 0057 claimed by in-flight PRs #54/#55 BEAAA-13360/13362) by Scanner Engineer at PR time (BEAAA-13390).
---

# ADR-0058 — Recurring integrity-checked backup posture for scan_log.sqlite3

## 1. Why

`scan_log.sqlite3` (`/var/lib/are-scanner/scan_log.sqlite3`) is the relational
store of forensic bundle URIs + `snapshot_hash` values — the evidence trail
behind every BackBond Score (159+ rows). Today the only copies are **ad-hoc
pre-deploy snapshots** (latest `20260710T200847Z-pre-43bc7ba`). Those protect
the deploy moment; they do nothing for steady-state loss or corruption between
deploys. My Supreme Law: no score's evidence may rest on a single
un-backed-up SQLite file.

## 2. Decision

Install an on-box, recurring, integrity-checked backup + a proven restore path.

1. **Online backup, not `cp`.** Snapshots are taken with
   `sqlite3.Connection.backup()` opening the source read-only. This is WAL-safe
   and consistent even while `/score` is writing; a filesystem `cp` of a live
   WAL DB can copy a torn page.
2. **Integrity gate.** Every snapshot runs `PRAGMA integrity_check` before it is
   accepted. A copy that fails is moved to `quarantine/`, never counted toward
   retention, and the run exits non-zero. A malformed *source* (backup API
   raises) is recorded as `FAILED` and exits non-zero.
3. **Backups carry their own evidence trail.** Append-only `MANIFEST.jsonl`
   records ts, sha256 (raw + gz), integrity result, and scan_log row count per
   snapshot — so a restore can be cross-checked and the backup history is
   auditable, mirroring the evidence-trail principle we apply to scores.
4. **Generational retention.** Hourly tier (keep 168 ≈ 7 days) + first-of-day
   promotion to a daily tier (keep 90). Phase-1 DB is KB-scale; disk cost is
   negligible and retention is deliberately generous.
5. **Egress-free.** The systemd unit runs with `PrivateNetwork=true` +
   `IPAddressDeny=any` — it *cannot* reach the network. Off-box durability, when
   wanted, is an **operator-mediated PULL** of the backup directory, never a
   box-initiated push. This is consistent with the srv1749248 containment wall
   (zero inbound SSH) and the default-deny egress posture (sibling ticket).
6. **Recurring restore-test.** A weekly non-destructive dry-run restores the
   newest snapshot into scratch, re-verifies sha256 vs manifest + integrity, and
   reads the row count. A backup never test-restored is not a backup.
7. **Stdlib only.** Both scripts use python3 stdlib exclusively, so they survive
   the ADR-0038 runtime venv slice and need no scanner deps.

## 3. Alternatives considered

- **`cp`/`rsync` of the live file** — rejected: torn-page risk on a live WAL DB.
- **Push to object storage on a schedule** — rejected: violates the egress lock;
  the box must not originate outbound. Operator-mediated pull preserves the wall.
- **Rely on ad-hoc pre-deploy snapshots** — rejected: they are the status quo
  and leave steady-state gaps between deploys (the whole reason for this ticket).
- **Litestream / continuous WAL streaming** — deferred: adds a daemon + a
  network sink; overkill for Phase-1 volume and harder to reconcile with the
  egress lock. Revisit if scan volume grows.

## 4. Scope / gate

No score, subscore, rubric, probe set, judge, or API contract changes.
Reproducibility band untouched (no scoring code runs). Therefore
**CTO-unilateral, no Underwriter gate**. Box-side install + restore-test proof
is OPERATOR-executed (containment wall — no agent SSH seat); CTO close-gates.

## 5. Rollback

Tier 0. `systemctl disable --now are-scanner-scanlog-backup.timer
are-scanner-scanlog-restoretest.timer`; optionally remove the backup root. The
live scoring path is never touched by install or rollback.

## 6. Verification (done at authoring)

The mechanism was self-tested off-box against a scratch scan_log (159 rows, WAL
mode): happy path produced an OK manifest line + daily promotion + a restore
dry-run reporting `verify_ok:true`/`sha256_match:true`/`integrity:ok`/159 rows;
a truncated (malformed) source produced a `FAILED` manifest line + exit 4 and
wrote no bad snapshot. On-box acceptance is the operator close-gate (runbook).
