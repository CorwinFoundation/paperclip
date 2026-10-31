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

# A fresh host needs its unit installed as well as its Python sources. Keep
# credential ownership and the existing runtime explicit; never install packages
# or copy a secret into the deployment payload.
if ! systemctl cat "$SERVICE" >/dev/null 2>&1; then
  test -s /etc/backbond/scan-intake.env || {
    echo 'missing operator configuration: /etc/backbond/scan-intake.env' >&2; exit 1;
  }
  test -s /etc/backbond/scan-mail.json || {
    echo 'missing sealed mail credential: /etc/backbond/scan-mail.json' >&2; exit 1;
  }
  test -s /etc/backbond/qa-review-scanner.json || {
    echo 'missing sealed scanner credential: /etc/backbond/qa-review-scanner.json' >&2; exit 1;
  }
  # Use a separate hash-locked runtime; the legacy scanner environment may be
  # incomplete and must not be modified by intake installation.
  if [ ! -x /opt/backbond/scan-venv/bin/uvicorn ]; then
    python3 -m venv /opt/backbond/scan-venv
  fi
  /opt/backbond/scan-venv/bin/python -m pip install --require-hashes -r "$HERE/requirements.txt"
  /opt/backbond/scan-venv/bin/python -c 'import fastapi, httpx, uvicorn, websockets'
  if ! id backbond-qa >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/backbond-scan-intake --shell /usr/sbin/nologin backbond-qa
  fi
  install -d -o backbond-qa -g backbond-qa -m 0750 /var/lib/backbond-scan-intake
  install -o root -g root -m 0644 "$HERE/systemd/backbond-scan-intake.service" "/etc/systemd/system/$SERVICE"
  systemctl daemon-reload
  systemctl enable "$SERVICE"
fi
# Bootstrap the one-step master deploy lane. The live sudo policy already
# permits only this helper; installing it here makes this the final bespoke
# root step needed for scan-intake releases.
if [ -f "$REPO_ROOT/scripts/deploy/scanner-deploy.sh" ]; then
  install -o root -g root -m 0755 "$REPO_ROOT/scripts/deploy/scanner-deploy.sh" /usr/local/sbin/scanner-deploy
fi
changed=0
EDGE_SOURCE="$HERE/server/edge_gateway.py"
if [ ! -f "$EDGE_SOURCE" ]; then
  EDGE_SOURCE="$HERE/../../api/edge_gateway.py"
fi
test -f "$EDGE_SOURCE"
install -d -o root -g root -m 0755 "$DEST"
for src in "$HERE"/server/*.py "$EDGE_SOURCE"; do
  name="$(basename "$src")"
  if [ -f "$DEST/$name" ] && cmp -s "$src" "$DEST/$name"; then
    echo "unchanged $name"; continue
  fi
  install -d -m 0700 "$BACKUP"
  [ -f "$DEST/$name" ] && cp -p "$DEST/$name" "$BACKUP/$name" && diff -u "$BACKUP/$name" "$src" > "$BACKUP/$name.diff" || true
  install -o root -g root -m 0644 "$src" "$DEST/$name"
  echo "updated $name"; changed=1
done
install -o root -g root -m 0755 "$HERE/report_mail_client.py" /usr/local/bin/backbond-report-mail
install -o root -g root -m 0755 "$HERE"/admit_retained_voice.py /usr/local/sbin/backbond-admit-retained-voice
# Release-specific WorkingDirectory overrides must not keep serving old code
# after the tracked updater installs a new runtime at DEST.
UNIT_OVERRIDE_DIR="/etc/systemd/system/$SERVICE.d"
WORKDIR_OVERRIDE="$UNIT_OVERRIDE_DIR/zz-tracked-runtime.conf"
WORKDIR_CONTENT="$(printf '[Service]\nWorkingDirectory=%s\n' "$DEST")"
if [ ! -f "$WORKDIR_OVERRIDE" ] || [ "$(cat "$WORKDIR_OVERRIDE")" != "$WORKDIR_CONTENT" ]; then
  install -d -o root -g root -m 0755 "$UNIT_OVERRIDE_DIR"
  printf '%s\n' "$WORKDIR_CONTENT" > "$WORKDIR_OVERRIDE"
  chmod 0644 "$WORKDIR_OVERRIDE"
  systemctl daemon-reload
  changed=1
fi
# Founder ruling, 2026-10-31: persistent routing for reports and access codes.
# Install after the existing grant EnvironmentFile; never copy or rewrite secrets.
RECIPIENT_ENV=/etc/backbond/founder-report-recipient.env
RECIPIENT_OVERRIDE="$UNIT_OVERRIDE_DIR/zzz-founder-report-recipient.conf"
if ! cmp -s "$HERE/founder-report-recipient.env" "$RECIPIENT_ENV"; then
  install -o root -g root -m 0644 "$HERE/founder-report-recipient.env" "$RECIPIENT_ENV"
  changed=1
fi
RECIPIENT_CONTENT="$(printf '[Service]\nEnvironmentFile=%s\n' "$RECIPIENT_ENV")"
if [ ! -f "$RECIPIENT_OVERRIDE" ] || [ "$(cat "$RECIPIENT_OVERRIDE")" != "$RECIPIENT_CONTENT" ]; then
  printf '%s\n' "$RECIPIENT_CONTENT" > "$RECIPIENT_OVERRIDE"
  chmod 0644 "$RECIPIENT_OVERRIDE"
  systemctl daemon-reload
  changed=1
fi
if [ "$changed" = 1 ]; then
  systemctl restart "$SERVICE"
  echo "backup: $BACKUP (diffs of the replaced live files are there)"
fi
systemctl is-active "$SERVICE"
