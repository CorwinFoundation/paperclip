#!/bin/bash
# Prune old Paperclip DB backups (backstop to the generator's own retention).
#
# The generator /usr/local/sbin/paperclip-db-backup.sh writes daily
# paperclip-YYYYMMDD-HHMMSS.dump (custom format) + .dump.sha256 into
# /mnt/paperclipdata/db-backups and self-prunes at 7 days. This cron is a
# COUNT-BASED backstop: keep the KEEP newest dumps regardless of age, delete
# the rest (and their .sha256). Protects against unbounded growth if the
# generator's mtime prune ever regresses again (it previously targeted the
# old paperclip-data/backups/*.sql.gz path and silently no-op'd — 2026-09-11).
#
# Usage: prune-paperclip-db-backups.sh [--dry-run]
set -uo pipefail
DIR="/mnt/paperclipdata/db-backups"
KEEP=7
DRY="${1:-}"
[ -d "$DIR" ] || exit 0

# All dumps, newest first; everything past the KEEP-th is prunable.
mapfile -t all < <(ls -1t "$DIR"/paperclip-*.dump 2>/dev/null)
[ "${#all[@]}" -le "$KEEP" ] && exit 0

for f in "${all[@]:$KEEP}"; do
  sz="$(du -h "$f" 2>/dev/null | cut -f1)"
  if [ "$DRY" = "--dry-run" ]; then
    echo "WOULD PRUNE  $sz  $f"
  else
    echo "pruned  $sz  $f"
    rm -f -- "$f" "$f.sha256"
  fi
done
exit 0
