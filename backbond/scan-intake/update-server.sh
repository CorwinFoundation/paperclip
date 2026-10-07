#!/usr/bin/env bash
# BEAAA-38895: install the tracked scan-intake server files and restart the service.
# Idempotent: identical files are skipped; changed files are backed up first.
# Usage (root): sudo bash backbond/scan-intake/update-server.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/backbond/scan-intake/server
BACKUP="/var/backups/backbond-scan-intake/$(date -u +%Y%m%dT%H%M%SZ)"
changed=0
for src in "$HERE"/server/app.py "$HERE"/server/qa_service.py "$HERE"/server/qa_runtime.py \
           "$HERE"/server/improvement_store.py "$HERE"/server/purge_retention.py; do
  name="$(basename "$src")"
  if [ -f "$DEST/$name" ] && cmp -s "$src" "$DEST/$name"; then
    echo "unchanged $name"; continue
  fi
  install -d -m 0700 "$BACKUP"
  [ -f "$DEST/$name" ] && cp -p "$DEST/$name" "$BACKUP/$name" && diff -u "$BACKUP/$name" "$src" > "$BACKUP/$name.diff" || true
  install -o root -g root -m 0644 "$src" "$DEST/$name"
  echo "updated $name"; changed=1
done
if [ "$changed" = 1 ]; then
  systemctl restart backbond-scan-intake.service
  echo "backup: $BACKUP (diffs of the replaced live files are there)"
fi
systemctl is-active backbond-scan-intake.service
