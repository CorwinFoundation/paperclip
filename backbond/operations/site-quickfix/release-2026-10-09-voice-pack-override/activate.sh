#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
STATE_SERVER="${SCAN_INTAKE_STATE_SERVER:-/var/lib/backbond-scan-intake}"
ACTIVE_SERVER="${SCAN_INTAKE_ACTIVE_SERVER:-/opt/backbond/scan-intake/server}"
SERVICE="${SCAN_INTAKE_SERVICE:-backbond-scan-intake.service}"
CONFIG_URL="${SCAN_INTAKE_CONFIG_URL:-https://api.backbond.ai/scan/v1/config}"

test "${1:-}" = install || { printf 'usage: sudo bash %s install\n' "$0" >&2; exit 2; }
test "$(id -u)" -eq 0
python3 -m py_compile "$HERE/patch_intake_pack.py"
for directory in "$STATE_SERVER" "$ACTIVE_SERVER"; do
  test -f "$directory/app.py"
  test -f "$directory/qa_service.py"
done

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_root="/var/backups/backbond-scan-intake/beaaa-39663-$stamp"
install -d -o root -g root -m 0700 "$backup_root"
prior_pid="$(systemctl show --property MainPID --value "$SERVICE")"

python3 "$HERE/patch_intake_pack.py" --server-dir "$STATE_SERVER" --backup-dir "$backup_root/state"
if test "$(readlink -f "$ACTIVE_SERVER")" != "$(readlink -f "$STATE_SERVER")"; then
  python3 "$HERE/patch_intake_pack.py" --server-dir "$ACTIVE_SERVER" --backup-dir "$backup_root/active"
fi
python3 -m py_compile "$ACTIVE_SERVER/app.py" "$ACTIVE_SERVER/qa_service.py"
systemctl restart "$SERVICE"
systemctl is-active --quiet "$SERVICE"
loaded_pid="$(systemctl show --property MainPID --value "$SERVICE")"
test "$loaded_pid" -gt 0
test "$loaded_pid" != "$prior_pid"
curl --fail --silent --show-error --max-time 20 "$CONFIG_URL" >/dev/null
printf 'intake_refreshed backup=%s prior_pid=%s loaded_pid=%s\n' "$backup_root" "$prior_pid" "$loaded_pid"
