#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
SITE="$HERE/site"
APP_TARGET="${SCAN_INTAKE_APP_TARGET:-/opt/backbond/scan-intake/server/app.py}"
INTAKE_DB="${SCAN_INTAKE_DB:-/var/lib/backbond-scan-intake/scan-intake.sqlite}"
SERVICE="${SCAN_INTAKE_SERVICE:-backbond-scan-intake.service}"
HEALTH_URL="${SCAN_INTAKE_HEALTH_URL:-https://api.backbond.ai/scan/v1/health}"

verify_sources() {
  cmp -s "$ROOT/backbond/scan-intake/web/scan-progress.js" "$SITE/scan/scan-progress.js"
  cmp -s "$ROOT/backbond/scan-intake/web/scan-progress.css" "$SITE/scan/scan-progress.css"
  python3 -m py_compile "$HERE/patch_live_progress.py"
  grep -q 'connection_proof:proof' "$SITE/scan/scan.js"
  grep -q "request('/scan-cancel'" "$SITE/scan/scan.js"
  grep -q "value.status==='finished'&&value.report_emailed===true" "$SITE/scan/scan.js"
}

activate_backend() {
  test "$(id -u)" -eq 0
  test -f "$APP_TARGET"
  test -f "$INTAKE_DB"
  PYTHONPATH="$(dirname "$APP_TARGET")" python3 -c 'from qa_service import IntakeStore; assert callable(getattr(IntakeStore, "scan", None))'
  active_count="$(python3 - "$INTAKE_DB" <<'PY'
import sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print(db.execute("SELECT count(*) FROM scans WHERE status IN ('reserved','queued','starting','running','dispatch_unknown')").fetchone()[0])
PY
)"
  printf 'active_intakes=%s\n' "$active_count"
  test "$active_count" = 0
  prior_pid="$(systemctl show --property MainPID --value "$SERVICE")"
  backup_dir="/var/backups/backbond-scan-intake/beaaa-39323-$(date -u +%Y%m%dT%H%M%SZ)"
  install -d -o root -g root -m 0700 "$backup_dir"
  python3 "$HERE/patch_live_progress.py" --target "$APP_TARGET" --backup "$backup_dir/app.py"
  python3 -m py_compile "$APP_TARGET"
  systemctl restart "$SERVICE"
  systemctl is-active --quiet "$SERVICE"
  loaded_pid="$(systemctl show --property MainPID --value "$SERVICE")"
  test "$loaded_pid" -gt 0
  test "$loaded_pid" != "$prior_pid"
  curl --fail --silent --show-error --max-time 20 "$HEALTH_URL" >/dev/null
  printf 'backend_activated app_sha256=%s backup=%s loaded_pid=%s\n' \
    "$(sha256sum "$APP_TARGET" | cut -d' ' -f1)" "$backup_dir" "$loaded_pid"
}

activate_site() {
  command -v netlify >/dev/null
  test -n "${NETLIFY_SITE_ID:-}"
  test -n "${NETLIFY_BASE_DIR:-}"
  test -d "$NETLIFY_BASE_DIR/scan"
  site_domain="$(netlify api getSite --data "{\"site_id\":\"$NETLIFY_SITE_ID\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("custom_domain", ""))')"
  test "$site_domain" = "backbond.ai"
  staging="$(mktemp -d)"
  trap 'rm -rf "$staging"' EXIT
  cp -a "$NETLIFY_BASE_DIR/." "$staging/"
  for asset in index.html scan.js scan-lifecycle.js scan-page.js scan-progress.js scan-progress.css; do
    curl --fail --silent --show-error --max-time 20 "https://backbond.ai/scan/$asset" > "$staging/live-$asset"
    cmp -s "$staging/live-$asset" "$NETLIFY_BASE_DIR/scan/$asset"
    install -m 0644 "$SITE/scan/$asset" "$staging/scan/$asset"
    rm "$staging/live-$asset"
  done
  netlify deploy --site "$NETLIFY_SITE_ID" --dir "$staging" --prod
  curl --fail --silent --show-error --max-time 20 https://backbond.ai/scan/scan-progress.js \
    | cmp - "$SITE/scan/scan-progress.js"
  printf 'site_activated progress_sha256=%s\n' \
    "$(sha256sum "$SITE/scan/scan-progress.js" | cut -d' ' -f1)"
}

verify_sources
case "$MODE" in
  backend) activate_backend ;;
  site) activate_site ;;
  verify) printf 'activation_sources_verified\n' ;;
  *) printf 'usage: %s verify|backend|site\n' "$0" >&2; exit 2 ;;
esac
