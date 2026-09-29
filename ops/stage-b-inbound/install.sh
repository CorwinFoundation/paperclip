#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
app_dir=/opt/backbond-stage-b/app
state_dir=/var/lib/backbond-stage-b
config_dir=/etc/backbond
secret_file=$config_dir/stage-b-inbound.env
receipt_secret_file=$config_dir/stage-b-receipts.env
unit_file=/etc/systemd/system/backbond-stage-b-inbound.service
receipt_unit_file=/etc/systemd/system/backbond-stage-b-receipts.service
snippet=/etc/nginx/snippets/backbond-stage-b-inbound.conf
vhost=/etc/nginx/sites-available/scanner.backbond.ai
include_line='    include /etc/nginx/snippets/backbond-stage-b-inbound.conf;'
backup=$(mktemp /tmp/backbond-stage-b-nginx.XXXXXX)
runtime_backup=$(mktemp -d /tmp/backbond-stage-b-runtime.XXXXXX)
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
id backbond-stage-b >/dev/null 2>&1 || useradd --system --home-dir "$state_dir" --shell /usr/sbin/nologin backbond-stage-b
install -d -o root -g root -m 0755 "$app_dir" "$config_dir"
install -d -o backbond-stage-b -g backbond-stage-b -m 0700 "$state_dir"
cp -a "$vhost" "$backup"
[ ! -d "$app_dir/stage_b_pilot" ] || cp -a "$app_dir" "$runtime_backup/app"
[ ! -f "$unit_file" ] || cp -a "$unit_file" "$runtime_backup/inbound.service"
[ ! -f "$receipt_unit_file" ] || cp -a "$receipt_unit_file" "$runtime_backup/receipts.service"
[ ! -f "$snippet" ] || cp -a "$snippet" "$runtime_backup/snippet.conf"
installed=1
rm -rf "$app_dir/stage_b_pilot"
cp -a "$repo_root/stage_b_pilot" "$app_dir/stage_b_pilot"
runtime_version=$(find "$repo_root/stage_b_pilot" -type f -print | LC_ALL=C sort | xargs sha256sum | sha256sum | cut -d' ' -f1)
printf '%s\n' "$runtime_version" >"$app_dir/.backbond-stage-b-version"
chown -R root:root "$app_dir/stage_b_pilot"
find "$app_dir/stage_b_pilot" -type d -exec chmod 0755 {} +
find "$app_dir/stage_b_pilot" -type f -exec chmod 0644 {} +
install -o root -g root -m 0644 "$repo_root/ops/stage-b-inbound/backbond-stage-b-inbound.service" "$unit_file"
install -o root -g root -m 0644 "$repo_root/ops/stage-b-inbound/backbond-stage-b-receipts.service" "$receipt_unit_file"
install -o root -g root -m 0644 "$repo_root/ops/stage-b-inbound/nginx-location.conf" "$snippet"

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
chown root:root "$secret_file"
chmod 0600 "$secret_file"

if [ ! -s "$receipt_secret_file" ]; then
    umask 077
    session_secret=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
    printf 'BB_DEMO_SESSION_SECRET=%s\n' "$session_secret" >"$receipt_secret_file"
fi
chown root:root "$receipt_secret_file"
chmod 0600 "$receipt_secret_file"

baseline_release=$(curl --fail --silent --output /dev/null --write-out '%{http_code}' https://scanner.backbond.ai/scanner/v1/release-identity)
baseline_sla=$(curl --fail --silent --output /dev/null --write-out '%{http_code}' https://scanner.backbond.ai/scanner/v1/sla)
python3 "$repo_root/ops/stage-b-inbound/patch_tls_vhost.py" "$vhost"
nginx -t
systemctl daemon-reload
systemctl enable backbond-stage-b-inbound.service
systemctl enable backbond-stage-b-receipts.service
systemctl restart backbond-stage-b-inbound.service
systemctl restart backbond-stage-b-receipts.service
systemctl reload nginx
systemctl is-active --quiet backbond-stage-b-inbound.service
systemctl is-active --quiet backbond-stage-b-receipts.service
curl --fail --silent http://127.0.0.1:8788/healthz
curl --fail --silent http://127.0.0.1:8789/stage-b-demo/receipts >/dev/null
test "$(curl --fail --silent --output /dev/null --write-out '%{http_code}' https://scanner.backbond.ai/scanner/v1/release-identity)" = "$baseline_release"
test "$(curl --fail --silent --output /dev/null --write-out '%{http_code}' https://scanner.backbond.ai/scanner/v1/sla)" = "$baseline_sla"
test "$(cat "$app_dir/.backbond-stage-b-version")" = "$runtime_version"
printf 'stage_b_runtime_version=%s\n' "$runtime_version"
rm -f "$backup"
rm -rf "$runtime_backup"
installed=0
trap - EXIT INT TERM
