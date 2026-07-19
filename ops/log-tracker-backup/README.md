# Log Tracker backup and restore test

Internal operations artifact for BEAAA-15623. The mechanism takes a WAL-safe
SQLite online backup hourly, retains 168 hourly and 90 daily snapshots, and
records checksum, integrity result, and per-table row counts in an append-only
manifest. A weekly timer decompresses the newest snapshot into scratch space
and verifies checksum plus `PRAGMA integrity_check`; it never touches the live
database.

The restore service keeps backup media read-only and uses `PrivateTmp=true`
for scratch decompression. Tests also exercise backup of a live WAL database
from a read-only source directory.

Install the script under `/opt/are-scanner/ops/log-tracker-backup`, create
`/var/lib/are-scanner/backups/log-tracker` mode 0700 owned by `are-scanner`,
install the four units, then enable both timers. Prime and prove the path with:

```sh
systemctl start backbond-log-tracker-backup.service
systemctl start backbond-log-tracker-restore-test.service
systemctl enable --now backbond-log-tracker-backup.timer
systemctl enable --now backbond-log-tracker-restore-test.timer
```

Acceptance evidence is an `OK` manifest record followed by restore-test JSON
with `verified: true`, `sha256_match: true`, and `integrity: "ok"`.

Rollback: disable and remove the four units and script. Preserve the backup
directory and manifest unless an approved retention action explicitly removes
them.
