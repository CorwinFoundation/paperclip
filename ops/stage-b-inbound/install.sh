#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
app_dir=/opt/backbond-stage-b/app
state_dir=/var/lib/backbond-stage-b
config_dir=/etc/backbond
secret_file=$config_dir/stage-b-inbound.env
unit_file=/etc/systemd/system/backbond-stage-b-inbound.service
snippet=/etc/nginx/snippets/backbond-stage-b-inbound.conf
vhost=/etc/nginx/sites-available/scanner.backbond.ai
include_line='    include /etc/nginx/snippets/backbond-stage-b-inbound.conf;'
backup=$(mktemp /tmp/backbond-stage-b-nginx.XXXXXX)
installed=0

rollback() {
    rc=$?
    trap - EXIT INT TERM
    if [ "$installed" -eq 1 ]; then
        cp -a "$backup" "$vhost"
        nginx -t && systemctl reload nginx || true
    fi
    rm -f "$backup"
    exit "$rc"
}
trap rollback EXIT INT TERM

[ "$(id -u)" -eq 0 ] || { echo 'ERROR root_required' >&2; exit 1; }
id backbond-stage-b >/dev/null 2>&1 || useradd --system --home-dir "$state_dir" --shell /usr/sbin/nologin backbond-stage-b
install -d -o root -g root -m 0755 "$app_dir" "$config_dir"
install -d -o backbond-stage-b -g backbond-stage-b -m 0700 "$state_dir"
rm -rf "$app_dir/stage_b_pilot"
cp -a "$repo_root/stage_b_pilot" "$app_dir/stage_b_pilot"
chown -R root:root "$app_dir/stage_b_pilot"
find "$app_dir/stage_b_pilot" -type d -exec chmod 0755 {} +
find "$app_dir/stage_b_pilot" -type f -exec chmod 0644 {} +
install -o root -g root -m 0644 "$repo_root/ops/stage-b-inbound/backbond-stage-b-inbound.service" "$unit_file"
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

cp -a "$vhost" "$backup"
baseline_release=$(curl --fail --silent --output /dev/null --write-out '%{http_code}' https://scanner.backbond.ai/scanner/v1/release-identity)
baseline_sla=$(curl --fail --silent --output /dev/null --write-out '%{http_code}' https://scanner.backbond.ai/scanner/v1/sla)
python3 "$repo_root/ops/stage-b-inbound/patch_tls_vhost.py" "$vhost"
installed=1

nginx -t
systemctl daemon-reload
systemctl enable --now backbond-stage-b-inbound.service
systemctl reload nginx
systemctl is-active --quiet backbond-stage-b-inbound.service
curl --fail --silent http://127.0.0.1:8788/healthz
test "$(curl --fail --silent --output /dev/null --write-out '%{http_code}' https://scanner.backbond.ai/scanner/v1/release-identity)" = "$baseline_release"
test "$(curl --fail --silent --output /dev/null --write-out '%{http_code}' https://scanner.backbond.ai/scanner/v1/sla)" = "$baseline_sla"
rm -f "$backup"
installed=0
trap - EXIT INT TERM
