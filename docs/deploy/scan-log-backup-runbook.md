---
title: scan_log backup & restore runbook — srv1749248
ticket: BEAAA-13386 (BEAAA-12895 criterion 6)
owner: CTO (design) / OPERATOR (box execution)
classification: CTO-unilateral ops tooling — no score, rubric, probe, or contract change → no UW gate
egress: on-box only; PrivateNetwork=true; default-deny egress respected; off-box durability = operator-mediated PULL only
date: 2026-07-10
---

# scan_log backup & restore runbook (srv1749248)

**BLUF.** `/var/lib/are-scanner/scan_log.sqlite3` holds the forensic bundle
URIs + snapshot hashes behind every BackBond Score (159+ rows). Ad-hoc
pre-deploy copies only protect the deploy moment. This installs a **recurring,
integrity-checked, egress-free** backup + a proven restore path so no score's
evidence rests on a single un-backed-up SQLite file.

## What it is

| Piece | Path | Cadence / retention |
|---|---|---|
| Backup script | `/opt/are-scanner/ops/scan-log-backup/backup_scan_log.py` | — |
| Restore script | `/opt/are-scanner/ops/scan-log-backup/restore_scan_log.py` | — |
| Backup timer | `are-scanner-scanlog-backup.timer` → `.service` | **hourly**, Persistent |
| Restore-test timer | `are-scanner-scanlog-restoretest.timer` → `.service` | **weekly** (Mon 04:17 UTC) |
| Snapshots (hourly tier) | `/var/lib/are-scanner/backups/scan_log/hourly/` | keep **168** (7d) |
| Snapshots (daily tier) | `/var/lib/are-scanner/backups/scan_log/daily/` | keep **90** (first-of-day) |
| Evidence ledger | `/var/lib/are-scanner/backups/scan_log/MANIFEST.jsonl` | append-only |
| Quarantine | `/var/lib/are-scanner/backups/scan_log/quarantine/` | bad copies, never counted |

Design invariants: **online backup** (`sqlite3.Connection.backup()` — WAL-safe,
no `cp` of a live DB); every snapshot **integrity-checked** (`PRAGMA
integrity_check == 'ok'`) before acceptance; each snapshot's sha256 (raw + gz),
integrity result, and scan_log row count recorded in `MANIFEST.jsonl`; **zero
network** (the unit runs with `PrivateNetwork=true` + `IPAddressDeny=any`);
stdlib-only python3 so it survives any venv slice (ADR-0038 runtime slice).

## Egress / containment posture

- Backups are written **on-box only**. The service has no network namespace.
- Off-box durability, when wanted, is an **operator-mediated PULL** of
  `/var/lib/are-scanner/backups/scan_log/` — never a box-initiated push. This
  is the same direction as deploy-record retrieval and does not require the box
  to originate any outbound connection. Consistent with the containment wall
  (zero inbound SSH) and the default-deny egress ticket.

## Install (OPERATOR, on srv1749248)

```bash
# 1. Place the mechanism (from the merged are-scanner tip).
sudo install -d -o are-scanner -g are-scanner -m 0755 /opt/are-scanner/ops/scan-log-backup
sudo install -o are-scanner -g are-scanner -m 0755 \
    ops/scan-log-backup/backup_scan_log.py  /opt/are-scanner/ops/scan-log-backup/
sudo install -o are-scanner -g are-scanner -m 0755 \
    ops/scan-log-backup/restore_scan_log.py /opt/are-scanner/ops/scan-log-backup/

# 2. Create the backup root (0700 — forensic evidence, are-scanner only).
sudo install -d -o are-scanner -g are-scanner -m 0700 /var/lib/are-scanner/backups/scan_log

# 3. Install + enable the units.
sudo install -m 0644 ops/scan-log-backup/systemd/*.service /etc/systemd/system/
sudo install -m 0644 ops/scan-log-backup/systemd/*.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now are-scanner-scanlog-backup.timer
sudo systemctl enable --now are-scanner-scanlog-restoretest.timer

# 4. Prime one backup immediately (don't wait for the first hourly tick).
sudo systemctl start are-scanner-scanlog-backup.service
sudo -u are-scanner cat /var/lib/are-scanner/backups/scan_log/MANIFEST.jsonl | tail -1
```

## Restore-test (non-destructive — this is the acceptance proof)

```bash
sudo -u are-scanner /usr/bin/python3 \
  /opt/are-scanner/ops/scan-log-backup/restore_scan_log.py \
  --backup-root /var/lib/are-scanner/backups/scan_log
```
PASS = JSON with `"verify_ok": true`, `"sha256_match": true`, `"integrity":
"ok"`, and a `row_count` ≥ the live scan_log row count. Exit 0. Capture this
output as the close-gate evidence.

## Restore-commit (DESTRUCTIVE — disaster recovery only)

Only when the live DB is lost/corrupt. Order matters — stop the writer first so
nothing races the restore:

```bash
sudo systemctl stop are-scanner                 # 1. stop the writer
sudo -u are-scanner /usr/bin/python3 \
  /opt/are-scanner/ops/scan-log-backup/restore_scan_log.py \
  --backup-root /var/lib/are-scanner/backups/scan_log \
  --snapshot <path/to/chosen.sqlite3.gz> \      # omit --snapshot to take latest
  --commit --i-stopped-the-service              # 2. verify + install at live path
# The script saves the current live DB to scan_log.sqlite3.pre-restore-<ts> first.
sudo systemctl start are-scanner                # 3. restart
curl -s localhost:8080/healthz                  # 4. confirm health
# 5. confirm a live GET /scan/{id} returns the documented envelope (BEAAA-13169).
```
`--commit` refuses to run without `--i-stopped-the-service`, and aborts if the
snapshot fails sha256/integrity verification.

## Monitoring

- `systemctl list-timers 'are-scanner-scanlog-*'` — next/last fire.
- A failed backup exits non-zero → `systemctl status` shows `failed`; the
  failure is also written to `MANIFEST.jsonl` (`"status":"FAILED"` or
  `"QUARANTINED"`). Wire `OnFailure=` to the box's existing alert unit if one
  exists; otherwise the weekly restore-test failing is the backstop signal.

## Close-gate (CTO verifies before BEAAA-13386 → done)

- **G1** Both timers `enabled` + `active (waiting)` in `systemctl list-timers`.
- **G2** ≥1 `"status":"OK"` line in `MANIFEST.jsonl` with a non-null `row_count`
  matching the live scan_log count and `integrity":"ok"`.
- **G3** Restore-test (dry-run) output shows `verify_ok:true`, `sha256_match:true`.
- **G4** Backup root is `0700 are-scanner:are-scanner`; snapshots present in
  `hourly/` and `daily/`.
- **G5** Unit shows `PrivateNetwork=true` (`systemctl show … -p PrivateNetwork`)
  — proves egress-free.
- **G6** Restore-commit path exercised at least once against a **scratch** live
  path (not the real DB) to prove `--commit` works end-to-end, OR the operator
  attests the dry-run + code review is sufficient for Phase 1.
```
