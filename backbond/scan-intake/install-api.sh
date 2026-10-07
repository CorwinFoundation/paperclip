#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root" >&2
  exit 1
fi

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install -d -o backbond-qa -g backbond-qa -m 0750 /opt/backbond/scan-intake/server /var/lib/backbond-scan-intake
install -o root -g root -m 0644 "$HERE"/server/*.py /opt/backbond/scan-intake/server/

if [ ! -f /etc/backbond/scan-intake.env ]; then
  if [ -f /etc/backbond/qa-review.env ]; then
    sed -e 's#/var/lib/backbond-qa-review/qa.sqlite#/var/lib/backbond-scan-intake/scan-intake.sqlite#' \
        -e 's/^QA_TERMS_VERSION=.*/QA_TERMS_VERSION=v0.2.2/' \
        /etc/backbond/qa-review.env > /etc/backbond/scan-intake.env
  else
    install -m 0640 "$HERE/scan-intake.env.example" /etc/backbond/scan-intake.env
  fi
fi
chown root:backbond-qa /etc/backbond/scan-intake.env
chmod 0640 /etc/backbond/scan-intake.env

if [ -f /var/lib/backbond-qa-review/qa.sqlite ] && [ ! -f /var/lib/backbond-scan-intake/scan-intake.sqlite ]; then
  systemctl stop backbond-qa-review.service || true
  /opt/backbond/scan-venv/bin/python -c 'import sqlite3; source=sqlite3.connect("file:/var/lib/backbond-qa-review/qa.sqlite?mode=ro",uri=True); target=sqlite3.connect("/var/lib/backbond-scan-intake/scan-intake.sqlite"); source.backup(target); target.close(); source.close()'
  chown -R backbond-qa:backbond-qa /var/lib/backbond-scan-intake
fi

test -s /etc/backbond/scan-mail.json || {
  echo 'missing /etc/backbond/scan-mail.json (JSON: {"provider":"resend","api_key":"..."})' >&2
  exit 1
}
install -m 0644 "$HERE"/systemd/backbond-scan-intake.service \
  "$HERE"/systemd/backbond-scan-intake-retention.service \
  "$HERE"/systemd/backbond-scan-intake-retention.timer /etc/systemd/system/
systemctl daemon-reload
systemctl disable --now backbond-qa-review.service || true
systemctl enable --now backbond-scan-intake.service backbond-scan-intake-retention.timer

# The retired /qa/v1 service is stopped only after the bundled access-log evidence is reviewed.
if grep -q '^requests=0$' "$HERE/evidence/partner-qa-access-summary.txt"; then
  systemctl disable --now backbond-qa.service
else
  echo 'partner-qa not stopped: zero-request evidence is absent' >&2
fi

curl --fail --silent http://127.0.0.1:8793/scan/v1/config >/dev/null
echo 'scan-intake installed; /scan/v1 is answering'
