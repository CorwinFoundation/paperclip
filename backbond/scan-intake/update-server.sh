#!/usr/bin/env bash
# BEAAA-38895: install the tracked scan-intake server files and restart the service.
# Idempotent: identical files are skipped; changed files are backed up first.
# Usage (root): sudo bash backbond/scan-intake/update-server.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/backbond/scan-intake/server
BACKUP="/var/backups/backbond-scan-intake/$(date -u +%Y%m%dT%H%M%SZ)"
SERVICE="${SCAN_INTAKE_SERVICE:-backbond-scan-intake.service}"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"

# Bootstrap the one-step master deploy lane. The live sudo policy already
# permits only this helper; installing it here makes this the final bespoke
# root step needed for scan-intake releases.
if [ -f "$REPO_ROOT/scripts/deploy/scanner-deploy.sh" ]; then
  install -o root -g root -m 0755 "$REPO_ROOT/scripts/deploy/scanner-deploy.sh" /usr/local/sbin/scanner-deploy
fi
changed=0
for src in "$HERE"/server/app.py "$HERE"/server/qa_service.py "$HERE"/server/report_extensions.py "$HERE"/server/qa_runtime.py \
           "$HERE"/server/quota_policy.py "$HERE"/server/improvement_store.py "$HERE"/server/purge_retention.py \
           "$HERE"/server/reconcile_terminal_failure.py "$HERE"/server/reconcile_internal_slot.py; do
  name="$(basename "$src")"
  if [ -f "$DEST/$name" ] && cmp -s "$src" "$DEST/$name"; then
    echo "unchanged $name"; continue
  fi
  install -d -m 0700 "$BACKUP"
  [ -f "$DEST/$name" ] && cp -p "$DEST/$name" "$BACKUP/$name" && diff -u "$BACKUP/$name" "$src" > "$BACKUP/$name.diff" || true
  install -o root -g root -m 0644 "$src" "$DEST/$name"
  echo "updated $name"; changed=1
done
install -o root -g root -m 0755 "$HERE"/admit_retained_voice.py /usr/local/sbin/backbond-admit-retained-voice
if [ "$changed" = 1 ]; then
  systemctl restart "$SERVICE"
  echo "backup: $BACKUP (diffs of the replaced live files are there)"
fi
systemctl is-active "$SERVICE"
