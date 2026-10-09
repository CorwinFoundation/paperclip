#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
app_dir=/opt/backbond-stage-b/app
state_dir=/var/lib/backbond-stage-b
config_dir=/etc/backbond
secret_file=$config_dir/stage-b-inbound.env
receipt_secret_file=$config_dir/stage-b-receipts.env
credential_file=$state_dir/receipt-logins.json
unit_file=/etc/systemd/system/backbond-stage-b-inbound.service
receipt_unit_file=/etc/systemd/system/backbond-stage-b-receipts.service
snippet=/etc/nginx/snippets/backbond-stage-b-inbound.conf
accept_helper=/usr/local/sbin/backbond-stage-b-accept-v1-report
acceptance_capture_helper=/usr/local/sbin/backbond-stage-b-capture-instinct-acceptance
status_helper=/opt/backbond-stage-b/bin/status.py
status_sudoers=/etc/sudoers.d/backbond-stage-b-status
vhost=/etc/nginx/sites-available/scanner.backbond.ai
include_line='    include /etc/nginx/snippets/backbond-stage-b-inbound.conf;'
backup=$(mktemp /tmp/backbond-stage-b-nginx.XXXXXX)
runtime_backup=$(mktemp -d /tmp/backbond-stage-b-runtime.XXXXXX)
chmod 0700 "$runtime_backup"
installed=0

rollback() {
    rc=$?
    trap - EXIT INT TERM
    if [ "$installed" -eq 1 ]; then
        cp -a "$backup" "$vhost"
        if [ -d "$runtime_backup/app" ]; then
            rm -rf "$app_dir"
            cp -a "$runtime_backup/app" "$app_dir"
        fi
        [ ! -f "$runtime_backup/inbound.service" ] || cp -a "$runtime_backup/inbound.service" "$unit_file"
        [ ! -f "$runtime_backup/receipts.service" ] || cp -a "$runtime_backup/receipts.service" "$receipt_unit_file"
        [ ! -f "$runtime_backup/snippet.conf" ] || cp -a "$runtime_backup/snippet.conf" "$snippet"
        if [ -d "$runtime_backup/state" ]; then
            rm -rf "$state_dir"
            cp -a "$runtime_backup/state" "$state_dir"
        fi
        [ ! -f "$runtime_backup/stage-b-inbound.env" ] || cp -a "$runtime_backup/stage-b-inbound.env" "$secret_file"
        [ ! -f "$runtime_backup/stage-b-receipts.env" ] || cp -a "$runtime_backup/stage-b-receipts.env" "$receipt_secret_file"
        systemctl daemon-reload || true
        systemctl restart backbond-stage-b-inbound.service backbond-stage-b-receipts.service || true
        nginx -t && systemctl reload nginx || true
    fi
    rm -f "$backup"
    rm -rf "$runtime_backup"
    exit "$rc"
}
trap rollback EXIT INT TERM

[ "$(id -u)" -eq 0 ] || { echo 'ERROR root_required' >&2; exit 1; }
[ -f "$repo_root/SOURCE_COMMIT" ] || {
    echo 'ERROR source_commit_file_missing' >&2
    exit 2
}
candidate_commit=$(cat "$repo_root/SOURCE_COMMIT")
printf '%s\n' "$candidate_commit" | grep -Eq '^[0-9a-f]{40}$' || {
    echo 'ERROR candidate_commit_missing_or_invalid' >&2
    exit 2
}
checkout_commit=$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || true)
if [ -n "$checkout_commit" ] && [ "$checkout_commit" != "$candidate_commit" ]; then
    echo 'ERROR source_commit_does_not_match_checkout' >&2
    exit 2
fi
id backbond-stage-b >/dev/null 2>&1 || useradd --system --home-dir "$state_dir" --shell /usr/sbin/nologin backbond-stage-b
install -d -o root -g root -m 0755 "$app_dir" "$config_dir"
install -d -o backbond-stage-b -g backbond-stage-b -m 0700 "$state_dir"
cp -a "$vhost" "$backup"
[ ! -d "$app_dir/stage_b_pilot" ] || cp -a "$app_dir" "$runtime_backup/app"
[ ! -f "$unit_file" ] || cp -a "$unit_file" "$runtime_backup/inbound.service"
[ ! -f "$receipt_unit_file" ] || cp -a "$receipt_unit_file" "$runtime_backup/receipts.service"
[ ! -f "$snippet" ] || cp -a "$snippet" "$runtime_backup/snippet.conf"
systemctl stop backbond-stage-b-receipts.service backbond-stage-b-inbound.service || true
[ ! -d "$state_dir" ] || cp -a "$state_dir" "$runtime_backup/state"
[ ! -f "$secret_file" ] || cp -a "$secret_file" "$runtime_backup/stage-b-inbound.env"
[ ! -f "$receipt_secret_file" ] || cp -a "$receipt_secret_file" "$runtime_backup/stage-b-receipts.env"
installed=1
rm -rf "$app_dir/stage_b_pilot"
cp -a "$repo_root/stage_b_pilot" "$app_dir/stage_b_pilot"
# BEAAA-38775: hash relative paths so identical code staged anywhere gets one digest.
runtime_version=$(cd "$repo_root" && find stage_b_pilot -name __pycache__ -prune -o -type f -print | LC_ALL=C sort | xargs sha256sum | sha256sum | cut -d' ' -f1)
printf '%s\n' "$runtime_version" >"$app_dir/.backbond-stage-b-version"
printf '%s\n' "$candidate_commit" >"$app_dir/DEPLOYED_COMMIT"
chown -R root:root "$app_dir/stage_b_pilot"
find "$app_dir/stage_b_pilot" -type d -exec chmod 0755 {} +
find "$app_dir/stage_b_pilot" -type f -exec chmod 0644 {} +
install -o root -g root -m 0644 "$repo_root/ops/stage-b-inbound/backbond-stage-b-inbound.service" "$unit_file"
install -o root -g root -m 0644 "$repo_root/ops/stage-b-inbound/backbond-stage-b-receipts.service" "$receipt_unit_file"
install -o root -g root -m 0644 "$repo_root/ops/stage-b-inbound/nginx-location.conf" "$snippet"
install -o root -g root -m 0755 "$repo_root/ops/stage-b-inbound/accept-v1-report.py" "$accept_helper"
install -o root -g root -m 0755 "$repo_root/ops/stage-b-inbound/capture-instinct-acceptance.py" "$acceptance_capture_helper"
# BEAAA-38728: read-only receipt summary; are-deploy may run exactly this script as backbond-stage-b.
install -d -o root -g root -m 0755 "$(dirname "$status_helper")"
install -o root -g root -m 0755 "$repo_root/ops/stage-b-inbound/status.py" "$status_helper"
status_sudoers_tmp=$(mktemp /tmp/backbond-stage-b-status-sudoers.XXXXXX)
# BEAAA-38775: group adm (only via sudo -g adm) lets the helper read the nginx access log and journal.
printf 'are-deploy ALL=(backbond-stage-b : adm) NOPASSWD: %s\n' "$status_helper" >"$status_sudoers_tmp"
visudo -cf "$status_sudoers_tmp" >/dev/null
install -o root -g root -m 0440 "$status_sudoers_tmp" "$status_sudoers"
rm -f "$status_sudoers_tmp"

if [ ! -s "$secret_file" ]; then
    [ -t 0 ] || { echo 'ERROR signing_key_requires_tty' >&2; exit 2; }
    printf 'Mailgun HTTP Webhook Signing Key: ' >&2
    stty -echo
    IFS= read -r signing_key
    stty echo
    printf '\n' >&2
    [ -n "$signing_key" ] || { echo 'ERROR signing_key_empty' >&2; exit 2; }
    umask 077
    printf 'BACKBOND_MAILGUN_SIGNING_KEY=%s\n' "$signing_key" >"$secret_file"
fi
if ! grep -q '^BB_HISTORY_ENCRYPTION_KEY_HEX=' "$secret_file"; then
    history_key=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
    printf 'BB_HISTORY_ENCRYPTION_KEY_HEX=%s\n' "$history_key" >>"$secret_file"
fi
grep -q '^BB_HISTORY_DB=' "$secret_file" || \
    printf 'BB_HISTORY_DB=%s/history.sqlite3\n' "$state_dir" >>"$secret_file"
grep -q '^BB_HISTORY_EXTERNAL_ENABLED=' "$secret_file" || \
    printf 'BB_HISTORY_EXTERNAL_ENABLED=0\n' >>"$secret_file"
grep -q '^BB_HISTORY_SYNTHETIC_ENABLED=' "$secret_file" || \
    printf 'BB_HISTORY_SYNTHETIC_ENABLED=1\n' >>"$secret_file"
grep -q '^BB_INSTINCT_STANDING_DB=' "$secret_file" || \
    printf 'BB_INSTINCT_STANDING_DB=%s/instinct-standing.sqlite3\n' "$state_dir" >>"$secret_file"
grep -q '^BB_INSTINCT_STANDING_SYNTHETIC_ENABLED=' "$secret_file" || \
    printf 'BB_INSTINCT_STANDING_SYNTHETIC_ENABLED=1\n' >>"$secret_file"
grep -q '^BB_STAGE_B_PUBLIC_BASE=' "$secret_file" || \
    printf 'BB_STAGE_B_PUBLIC_BASE=https://scanner.backbond.ai/stage-b-demo\n' >>"$secret_file"
chown root:root "$secret_file"
chmod 0600 "$secret_file"

if [ ! -s "$receipt_secret_file" ]; then
    session_secret=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
    umask 077
    {
        printf 'BB_DEMO_SESSION_SECRET=%s\n' "$session_secret"
        printf 'BB_DEMO_CREDENTIAL_FILE=%s\n' "$credential_file"
    } >"$receipt_secret_file"
fi
chown root:root "$receipt_secret_file"
chmod 0600 "$receipt_secret_file"
grep -Eq '^BB_DEMO_SESSION_SECRET=.{32,}$' "$receipt_secret_file" || { echo 'ERROR receipt_session_secret_missing' >&2; exit 2; }
grep -Fxq "BB_DEMO_CREDENTIAL_FILE=$credential_file" "$receipt_secret_file" || { echo 'ERROR receipt_credential_file_invalid' >&2; exit 2; }
grep -q '^BB_ONBOARDING_DB=' "$receipt_secret_file" || printf 'BB_ONBOARDING_DB=%s/onboarding.sqlite3\n' "$state_dir" >>"$receipt_secret_file"
grep -q '^BB_INBOUND_DB=' "$receipt_secret_file" || printf 'BB_INBOUND_DB=%s/inbound.sqlite3\n' "$state_dir" >>"$receipt_secret_file"
grep -q '^BB_STAGE_B_PUBLIC_BASE=' "$receipt_secret_file" || printf 'BB_STAGE_B_PUBLIC_BASE=https://scanner.backbond.ai/stage-b-demo\n' >>"$receipt_secret_file"
grep -q '^BB_HISTORY_DB=' "$receipt_secret_file" || printf 'BB_HISTORY_DB=%s/history.sqlite3\n' "$state_dir" >>"$receipt_secret_file"
if ! grep -q '^BB_HISTORY_ENCRYPTION_KEY_HEX=' "$receipt_secret_file"; then
    grep '^BB_HISTORY_ENCRYPTION_KEY_HEX=' "$secret_file" >>"$receipt_secret_file"
fi
for key in BACKBOND_MAILGUN_MESSAGES_URL BACKBOND_MAILGUN_API_KEY BACKBOND_MAILGUN_FROM; do
    grep -q "^${key}=" "$receipt_secret_file" || grep "^${key}=" "$secret_file" >>"$receipt_secret_file" || true
done
if [ ! -e "$credential_file" ]; then
    install -o backbond-stage-b -g backbond-stage-b -m 0600 /dev/null "$credential_file"
    printf '{}\n' >"$credential_file"
fi
chown backbond-stage-b:backbond-stage-b "$credential_file"
chmod 0600 "$credential_file"

# Prove the installed package is traversable and can create a probe as the
# service UID before restarting either live service.
probe_preflight_db=$state_dir/transport-probe-preflight.sqlite3
rm -f "$probe_preflight_db"
runuser -u backbond-stage-b -- sh -c 'cd / && env PYTHONPATH="$1" /usr/bin/python3 -c '\''import sys; from stage_b_pilot.export_intake import ExportIntakeStore; s=ExportIntakeStore(sys.argv[1], encryption_key=b"x"*32); print(s.issue_transport_probe()); s.close()'\'' "$2"' sh "$app_dir" "$probe_preflight_db" >/dev/null
rm -f "$probe_preflight_db"

baseline_release=$(curl --fail --silent --output /dev/null --write-out '%{http_code}' https://scanner.backbond.ai/scanner/v1/release-identity)
baseline_sla=$(curl --fail --silent --output /dev/null --write-out '%{http_code}' https://scanner.backbond.ai/scanner/v1/sla)
python3 "$repo_root/ops/stage-b-inbound/patch_tls_vhost.py" "$vhost"
nginx -t
systemctl daemon-reload
systemctl enable backbond-stage-b-inbound.service backbond-stage-b-receipts.service
systemctl restart backbond-stage-b-inbound.service
systemctl restart backbond-stage-b-receipts.service
systemctl reload nginx
systemctl is-active --quiet backbond-stage-b-inbound.service
systemctl is-active --quiet backbond-stage-b-receipts.service
transport_probe_id=$(runuser -u backbond-stage-b -- sh -c 'cd / && env PYTHONPATH="$1" /usr/bin/python3 -c '\''import sys; from stage_b_pilot.export_intake import ExportIntakeStore; s=ExportIntakeStore(sys.argv[1], encryption_key=b"x"*32); print(s.issue_transport_probe()); s.close()'\'' "$2"' sh "$app_dir" "$state_dir/history.sqlite3")
curl --fail --silent --retry 10 --retry-delay 1 --retry-connrefused http://127.0.0.1:8788/healthz
curl --fail --silent --retry 10 --retry-delay 1 --retry-connrefused http://127.0.0.1:8789/stage-b-demo/receipts >/dev/null
curl --fail --silent --retry 10 --retry-delay 1 --retry-connrefused http://127.0.0.1:8789/stage-b-demo/history >/dev/null
test "$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8788/history/state)" = "401"
test "$(curl --fail --silent --output /dev/null --write-out '%{http_code}' https://scanner.backbond.ai/scanner/v1/release-identity)" = "$baseline_release"
test "$(curl --fail --silent --output /dev/null --write-out '%{http_code}' https://scanner.backbond.ai/scanner/v1/sla)" = "$baseline_sla"
test "$(cat "$app_dir/.backbond-stage-b-version")" = "$runtime_version"
test "$(cat "$app_dir/DEPLOYED_COMMIT")" = "$candidate_commit"
printf 'stage_b_candidate=%s stage_b_runtime_version=%s inbound_main_pid=%s receipts_main_pid=%s\n' \
    "$candidate_commit" "$runtime_version" \
    "$(systemctl show --property MainPID --value backbond-stage-b-inbound.service)" \
    "$(systemctl show --property MainPID --value backbond-stage-b-receipts.service)"
printf 'transport_probe_id=%s transport_probe_recipient=%s\n' \
    "$transport_probe_id" 'history+export@inbound.backbond.ai'
rm -f "$backup"
rm -rf "$runtime_backup"
installed=0
trap - EXIT INT TERM
