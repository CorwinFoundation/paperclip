#!/usr/bin/env bash
# Idempotently withhold one public scan report at the Caddy edge.
set -Eeuo pipefail

TOKEN=${1:-}
CADDYFILE=${CADDYFILE:-/etc/caddy/Caddyfile}
CADDY=${CADDY:-caddy}
SYSTEMCTL=${SYSTEMCTL:-systemctl}

[[ "$TOKEN" =~ ^[a-f0-9]{32}$ ]] || { echo "usage: withhold-scan-report <32-hex-token>" >&2; exit 2; }
marker="@withheld_report_${TOKEN} path /scan/v1/reports/${TOKEN} /scan/v1/reports/${TOKEN}/*"
grep -Fqx "    $marker" "$CADDYFILE" && exit 0
grep -Fq '    @scan_intake path /scan/v1/*' "$CADDYFILE" || { echo "scan intake matcher not found" >&2; exit 1; }

backup="${CADDYFILE}.before-withhold-${TOKEN}-$(date -u +%Y%m%dT%H%M%SZ)"
tmp=$(mktemp "${CADDYFILE}.withhold.XXXXXX")
trap 'rm -f -- "$tmp"' EXIT
cp -a -- "$CADDYFILE" "$backup"
awk -v marker="$marker" '
  /^    @scan_intake path \/scan\/v1\/\*/ && !done {
    print "    " marker
    print "    handle @withheld_report_" substr(marker, 18, 32) " {"
    print "        respond 404"
    print "    }"
    done=1
  }
  { print }
' "$CADDYFILE" >"$tmp"
chmod --reference="$CADDYFILE" "$tmp"
chown --reference="$CADDYFILE" "$tmp"
mv -f -- "$tmp" "$CADDYFILE"

if ! "$CADDY" validate --config "$CADDYFILE" || ! "$SYSTEMCTL" reload caddy; then
  cp -a -- "$backup" "$CADDYFILE"
  "$CADDY" validate --config "$CADDYFILE" >/dev/null
  "$SYSTEMCTL" reload caddy
  echo "Caddy update failed; restored $backup" >&2
  exit 1
fi
echo "withheld report $TOKEN; backup=$backup"
