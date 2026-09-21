#!/usr/bin/env bash
set -euo pipefail

[ "$#" = 0 ] || { echo "usage: backbond-listener-status" >&2; exit 2; }

set -a
# shellcheck disable=SC1091
. /etc/backbond/appliance-ingest.env
set +a

base=http://127.0.0.1:8787

printf 'receiver='; systemctl is-active backbond-appliance-ingest.service
printf 'timer='; systemctl is-active backbond-appliance.timer
printf 'appliance='; systemctl show backbond-appliance.service \
  -p ActiveState -p SubState -p Result -p ExecMainStatus \
  -p ExecMainStartTimestamp -p ExecMainExitTimestamp --value | paste -sd, -
printf 'health_http='
curl -fsS -o /dev/null -w '%{http_code}\n' --max-time 5 "$base/healthz"
printf 'metrics='
curl -fsS --max-time 5 \
  -H "Authorization: Bearer $BACKBOND_INGEST_TOKEN" \
  -H "X-BackBond-Tenant: $BACKBOND_TENANT_ID" \
  "$base/metrics"
