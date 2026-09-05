#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
ROOT="${BACKBOND_INSTALL_ROOT:-}"
DRY_RUN="${DRY_RUN:-0}"
ARCHIVE="$REPO/ops/paperclip/numbat/backbond-appliance-0.1.0.zip"
EXPECTED_SHA=dbef6e73a715adda40e7697c070a96b7844398e09c7925926b8e541e5c87ef8a
OPT="$ROOT/opt/backbond"
ETC="$ROOT/etc/backbond"
VAR="$ROOT/var/lib/backbond"
UNITS="$ROOT/etc/systemd/system"
BACKUP="$VAR/install-backups"
APPLIANCE_ID="${BACKBOND_APPLIANCE_ID:-ariclaw-1}"
TENANT_ID="${BACKBOND_TENANT_ID:-backbond}"
SOURCE_ID="${BACKBOND_SOURCE_ID:-paperclip-codex-rollouts}"
BINDING_ID="${BACKBOND_BINDING_ID:-ariclaw}"
BINDING_FROM="${BACKBOND_BINDING_EFFECTIVE_FROM:-1970-01-01T00:00:00Z}"
TOKEN="${BACKBOND_SHIP_TOKEN:-}"

die() { printf 'ERROR %s\n' "$*" >&2; exit 1; }
target() { printf '%s' "$ROOT$1"; }
run() { if [ "$DRY_RUN" = 1 ]; then printf 'DRY-RUN'; printf ' %q' "$@"; printf '\n'; else "$@"; fi; }
put() {
  local dst="$1" mode="$2" owner="$3" tmp
  if [ "$DRY_RUN" = 1 ]; then cat >/dev/null; printf 'DRY-RUN write %s mode=%s owner=%s\n' "$dst" "$mode" "$owner"; return; fi
  tmp="$(mktemp)"; cat >"$tmp"
  if [ -f "$dst" ] && cmp -s "$tmp" "$dst"; then rm -f "$tmp"; chmod "$mode" "$dst"; return; fi
  if [ -f "$dst" ]; then mkdir -p "$BACKUP"; cp -a "$dst" "$BACKUP/$(basename "$dst").$(date -u +%Y%m%dT%H%M%SZ)"; fi
  install -D -m "$mode" "$tmp" "$dst"; rm -f "$tmp"
  if [ -z "$ROOT" ]; then chown "$owner" "$dst"; fi
}

[ "$(id -u)" = 0 ] || [ -n "$ROOT" ] || die 'run as root'
command -v sha256sum >/dev/null || die 'sha256sum is required'
command -v unzip >/dev/null || die 'unzip is required'
[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" = "$EXPECTED_SHA" ] || die 'sealed archive checksum mismatch'
[ -n "$TOKEN" ] || die 'set BACKBOND_SHIP_TOKEN'

if [ "$DRY_RUN" = 1 ]; then
  printf 'DRY-RUN verified sealed archive %s\n' "$EXPECTED_SHA"
  printf 'DRY-RUN would install receiver, appliance, configuration, units, and timer under %s\n' "${ROOT:-/}"
  printf 'INSTALL sudo BACKBOND_SHIP_TOKEN=<token> BACKBOND_TENANT_ID=%s BACKBOND_BINDING_ID=%s %s\n' "$TENANT_ID" "$BINDING_ID" "$REPO/ops/paperclip/numbat/install-listener-ariclaw.sh"
  exit 0
fi

if [ -z "$ROOT" ]; then
  getent group backbond >/dev/null || groupadd --system backbond
  id backbond >/dev/null 2>&1 || useradd --system --gid backbond --home-dir /var/lib/backbond --shell /usr/sbin/nologin backbond
fi
install -d -m 0755 "$OPT" "$UNITS"
install -d -m 0750 "$ETC" "$VAR" "$BACKUP"
[ -n "$ROOT" ] || chown backbond:backbond "$VAR"

stage="$(mktemp -d)"; trap 'rm -rf "$stage"' EXIT
unzip -q "$ARCHIVE" -d "$stage"
rm -rf "$OPT/appliance-0.1.0.new"
cp -a "$stage/backbond-appliance" "$OPT/appliance-0.1.0.new"
if [ ! -d "$OPT/appliance-0.1.0" ] || ! diff -qr "$OPT/appliance-0.1.0.new" "$OPT/appliance-0.1.0" >/dev/null; then
  [ ! -d "$OPT/appliance-0.1.0" ] || cp -a "$OPT/appliance-0.1.0" "$BACKUP/appliance-0.1.0.$(date -u +%Y%m%dT%H%M%SZ)"
  rm -rf "$OPT/appliance-0.1.0"; mv "$OPT/appliance-0.1.0.new" "$OPT/appliance-0.1.0"
else rm -rf "$OPT/appliance-0.1.0.new"; fi

for migration in "$REPO"/tools/log-tracker/migrations/*.sql; do install -D -m 0644 "$migration" "$OPT/migrations/$(basename "$migration")"; done
if [ "${BACKBOND_INSTALL_SKIP_RUNTIME:-0}" != 1 ]; then
  [ -x "$OPT/venv/bin/python" ] || python3 -m venv "$OPT/venv"
  "$OPT/venv/bin/pip" install "$REPO/tools/log-tracker" -r "$OPT/appliance-0.1.0/requirements.txt"
fi

put "$ETC/config.yaml" 0640 root:backbond <<EOF
appliance_id: "$APPLIANCE_ID"
evidence_tier: "log-inferred"
reader: {name: "codex_rollout", path: "/mnt/paperclipdata/dot-paperclip/instances/default/companies"}
rules: {pack: "coding_agent.cel.yaml", allowlist: "paperclip_allowlist.cel.yaml"}
signing: {key_path: "/var/lib/backbond/appliance.key", key_id: "$APPLIANCE_ID-key-1"}
state_dir: "/var/lib/backbond/appliance-state"
ship: {mode: "http", url: "http://127.0.0.1:8787/ingest", max_events_per_batch: 5000, gzip: true, retries: 4, backoff_base_sec: 0.5, timeout_sec: 30}
producer: {collector_version: "appliance-0.1.0", evidence_profile_version: "1.0", scanner_version: "na-appliance", corpus_version: "na-appliance", rule_pack_version: "0.1", seed: 0}
EOF
put "$ETC/appliance-ingest.env" 0600 root:root <<EOF
BACKBOND_INGEST_TOKEN=$TOKEN
BACKBOND_SHIP_TOKEN=$TOKEN
EOF

if [ "${BACKBOND_INSTALL_SKIP_RUNTIME:-0}" = 1 ]; then
  PUBLIC_KEY="test-public-key"
else
  install -d -o backbond -g backbond -m 0750 "$VAR/appliance-state"
  PUBLIC_KEY="$(BACKBOND_CONFIG=/etc/backbond/config.yaml "$OPT/venv/bin/python" -m appliance.main enroll -c "$ETC/config.yaml" | python3 -c 'import json,sys; print(json.load(sys.stdin)["public_key_pem"], end="")')"
fi
python3 - "$ETC/appliance-enrollment.json.tmp" "$APPLIANCE_ID" "$TENANT_ID" "$SOURCE_ID" "$BINDING_ID" "$BINDING_FROM" "$PUBLIC_KEY" <<'PY'
import json,sys
p, appliance, tenant, source, binding, effective, public = sys.argv[1:]
with open(p, "w") as f: json.dump({"appliance_id":appliance,"tenant_id":tenant,"source_id":source,"public_key_pem":public,"token_env":"BACKBOND_INGEST_TOKEN","binding_id":binding,"binding_effective_from":effective}, f, indent=2, sort_keys=True)
PY
put "$ETC/appliance-enrollment.json" 0640 root:backbond < "$ETC/appliance-enrollment.json.tmp"; rm -f "$ETC/appliance-enrollment.json.tmp"

put "$UNITS/backbond-appliance-ingest.service" 0644 root:root < "$REPO/ops/paperclip/numbat/backbond-appliance-ingest.service"
put "$UNITS/backbond-appliance.service" 0644 root:root <<'EOF'
[Unit]
Description=BackBond AriClaw no-content appliance
After=backbond-appliance-ingest.service
[Service]
Type=oneshot
User=backbond
Group=backbond
EnvironmentFile=/etc/backbond/appliance-ingest.env
ExecStart=/opt/backbond/venv/bin/python -m appliance.main run -c /etc/backbond/config.yaml
WorkingDirectory=/opt/backbond/appliance-0.1.0
ProtectSystem=strict
ReadWritePaths=/var/lib/backbond
PrivateTmp=true
NoNewPrivileges=true
EOF
put "$UNITS/backbond-appliance.timer" 0644 root:root <<'EOF'
[Unit]
Description=Run BackBond appliance every five minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true
[Install]
WantedBy=timers.target
EOF

if [ -z "$ROOT" ]; then systemctl daemon-reload; systemctl enable --now backbond-appliance-ingest.service backbond-appliance.timer; fi
printf 'INSTALLED listener and five-minute appliance timer\n'
printf 'CHECK systemctl is-active backbond-appliance-ingest.service backbond-appliance.timer\n'
printf 'HEALTH curl -fsS http://127.0.0.1:8787/healthz\n'
printf 'METRICS curl -fsS -H "Authorization: Bearer <token>" -H "X-BackBond-Tenant: %s" http://127.0.0.1:8787/metrics\n' "$TENANT_ID"
printf 'ROLLBACK systemctl disable --now backbond-appliance.timer backbond-appliance.service backbond-appliance-ingest.service; restore files from %s\n' "$BACKUP"
