# scan_log backup — BEAAA-13386 (BEAAA-12895 criterion 6)

Recurring, integrity-checked, **egress-free** backup + proven restore path for
`/var/lib/are-scanner/scan_log.sqlite3` (the forensic evidence trail behind
every BackBond Score). CTO-unilateral ops tooling — no score/contract change.

- `backup_scan_log.py` — hourly online (WAL-safe) backup, integrity-gated,
  manifest-logged, generational retention. stdlib-only.
- `restore_scan_log.py` — `--dry-run` (default, non-destructive restore-test) /
  `--commit` (operator-gated disaster recovery).
- `systemd/` — hourly backup timer + weekly restore-test timer; both
  `PrivateNetwork=true` (physically egress-free).

Install + close-gate: `../../docs/deploy/scan-log-backup-runbook.md`.
Design rationale: `../../docs/adr/adr-0058-scan-log-backup-posture.md`.
