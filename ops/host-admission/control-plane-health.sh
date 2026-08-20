#!/usr/bin/env bash
# BEAAA-24049 — listener-aware control-plane health probe.
#
# Why this exists: PM2 reported `online` for the whole of the BEAAA-24046
# outage. `online` means "the supervised PID exists", which is not readiness.
# Measured on this host at 2026-08-17T19:3xZ while load was 35.3 and swap was
# 3.9/4.0 GiB used:
#
#   /                       200 in 0.006s
#   /health                 200 in 0.012s
#   /api/health             200 in 1.510s
#   /api/issues/BEAAA-24049 200 in 30.242s   (and 000 at --max-time 45 minutes earlier)
#
# A probe of `/` or of PM2 state is false-quiet here. A short-timeout probe of
# a DB-backed route is the opposite failure: it declares "down" for a server
# that is merely slow, and a restart under swap thrash discards in-flight agent
# runs and makes recovery worse.
#
# So health is reported as three tiers plus a latency band, and the tiers are
# not interchangeable:
#
#   L1 TCP     listener bound on PAPERCLIP_LISTEN_PORT   -> process liveness
#   L2 ROUTER  /api/health responds                      -> express app mounted
#   L3 READY   DB-backed authenticated read responds     -> actually serving work
#
# Exit codes are the contract consumed by control-plane-warden.sh:
#   0 HEALTHY  L3 ok within READY_WARN_S
#   1 DEGRADED L3 ok but slow, or L2 ok and L3 timed out  -> SHED, never restart
#   2 DOWN     L1 ok but L2 failed                        -> restart candidate
#   3 DOWN     L1 failed, nothing listening               -> restart candidate
#
# Read-only. Makes no change to the host.

set -uo pipefail

PORT="${PAPERCLIP_LISTEN_PORT:-3100}"
HOST="${PAPERCLIP_LISTEN_HOST:-127.0.0.1}"
BASE="http://${HOST}:${PORT}"

# Generous ceilings on purpose. Under the observed degraded state a healthy
# server answered the DB path in 30s; a 10s ceiling would have restarted it.
READY_WARN_S="${READY_WARN_S:-5}"
READY_MAX_S="${READY_MAX_S:-120}"
ROUTER_MAX_S="${ROUTER_MAX_S:-30}"
READY_PATH="${READY_PATH:-/api/issues/BEAAA-24049}"

json_escape() { printf '%s' "${1-}" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

# L1 -- is anything actually bound? Port in hex, matched in /proc/net/tcp.
# ss(8) is not guaranteed present in every container image on this host.
hexport=$(printf '%04X' "$PORT")
if grep -qiE ":${hexport} .* 0A " /proc/net/tcp /proc/net/tcp6 2>/dev/null; then
  l1=up
else
  l1=down
fi

if [ "$l1" = down ]; then
  printf '{"tier":"L1","state":"DOWN","reason":"no listener bound on %s","port":%s}\n' "$BASE" "$PORT"
  exit 3
fi

# L2 -- router mounted.
t_router=$(curl -s -o /dev/null -w '%{time_total}' --max-time "$ROUTER_MAX_S" "$BASE/api/health" 2>/dev/null)
c_router=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$ROUTER_MAX_S" "$BASE/api/health" 2>/dev/null)
if [ "${c_router:-000}" != "200" ]; then
  printf '{"tier":"L2","state":"DOWN","reason":"listener bound but /api/health returned %s","router_s":%s}\n' \
    "${c_router:-000}" "${t_router:-0}"
  exit 2
fi

# L3 -- real DB-backed read. Requires a token; without one we cannot claim
# readiness, and we must not silently downgrade to L2 and call that healthy.
if [ -z "${PAPERCLIP_API_KEY:-}" ]; then
  printf '{"tier":"L2","state":"DEGRADED","reason":"no PAPERCLIP_API_KEY, readiness unprovable","router_s":%s}\n' \
    "${t_router:-0}"
  exit 1
fi

read -r c_ready t_ready <<EOF
$(curl -s -o /dev/null -w '%{http_code} %{time_total}' --max-time "$READY_MAX_S" \
   -H "Authorization: Bearer ${PAPERCLIP_API_KEY}" "$BASE$READY_PATH" 2>/dev/null || echo "000 $READY_MAX_S")
EOF

# Context that makes a DEGRADED verdict actionable rather than just a number.
swap_used_kb=$(awk '/SwapTotal/{t=$2} /SwapFree/{f=$2} END{print t-f}' /proc/meminfo 2>/dev/null)
swap_total_kb=$(awk '/SwapTotal/{print $2}' /proc/meminfo 2>/dev/null)
io_full=$(awk '/^full/{for(i=1;i<=NF;i++) if($i ~ /^avg60=/){sub("avg60=","",$i); print $i}}' /proc/pressure/io 2>/dev/null)
loadavg=$(awk '{print $1}' /proc/loadavg 2>/dev/null)
seats=$(pgrep -fc 'local/bin/claude --print|codex exec --json' 2>/dev/null || echo 0)

ctx=$(printf '"load":%s,"io_full_avg60":%s,"swap_used_mb":%s,"swap_total_mb":%s,"agent_seats":%s' \
  "${loadavg:-0}" "${io_full:-0}" "$(( ${swap_used_kb:-0} / 1024 ))" "$(( ${swap_total_kb:-0} / 1024 ))" "${seats:-0}")

if [ "${c_ready:-000}" != "200" ]; then
  printf '{"tier":"L3","state":"DEGRADED","reason":"router up but DB-backed read did not complete in %ss","ready_code":"%s",%s}\n' \
    "$READY_MAX_S" "${c_ready:-000}" "$ctx"
  exit 1
fi

slow=$(awk -v a="${t_ready:-0}" -v b="$READY_WARN_S" 'BEGIN{print (a>b)?1:0}')
if [ "$slow" = "1" ]; then
  printf '{"tier":"L3","state":"DEGRADED","reason":"serving but starved","ready_s":%s,"warn_s":%s,%s}\n' \
    "${t_ready:-0}" "$READY_WARN_S" "$ctx"
  exit 1
fi

printf '{"tier":"L3","state":"HEALTHY","ready_s":%s,%s}\n' "${t_ready:-0}" "$ctx"
exit 0
