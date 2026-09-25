#!/usr/bin/env bash
set -euo pipefail

[ "$#" = 0 ] || { echo "usage: backbond-listener-status" >&2; exit 2; }

# SECURITY -- this sourcing is the load-bearing part of the sudoers grant.
# Under /etc/sudoers.d/backbond-listener-status this script runs as root, and
# sourcing is code execution, so anything written into appliance-ingest.env runs
# as root too. What makes that safe is a file permission, not any logic below:
# appliance-ingest.env is 0600 root:root inside /etc/backbond, which is
# root:backbond 0750. Group backbond therefore gets r-x on the directory and
# nothing on the file -- no write bit on either, so a group member can neither
# edit the file nor replace it by unlinking it. Joining group backbond (which
# the BEAAA-33051 install does, to read the receipts DB) does not breach this.
# What would breach it: making appliance-ingest.env agent-writable, or giving
# group backbond write on /etc/backbond. Either turns this drop-in into an
# arbitrary root shell. Re-check those two modes before widening anything here.
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
