#!/bin/bash
# BEAAA-24540 CTO-owned: retention sweep for the run-log offload store.
#
# Policy (see BEAAA-24540 for the reasoning):
#   Run-log .ndjson bodies age out at RETENTION_DAYS. The durable audit record
#   is the Postgres heartbeat_runs row -- id, agent, timing, status, error code,
#   log_bytes and log_sha256 all survive the deletion, so the run stays provable
#   and the deleted body stays content-addressed. Only the verbatim stdout/stderr
#   text is destroyed, which is the entire cleartext-credential exposure.
#
# Deletion is file-only. No DB mutation, no paperclipai restart, no migration.
# Every reader of this store already degrades cleanly on a missing file:
#   - services/issues.ts readRunLogText      -> catches 404, warns, returns partial
#   - services/feedback.ts readFullRunLog    -> .catch(() => null)
#   - services/recovery/service.ts           -> try/catch, returns ""
#   - services/heartbeat.ts readLog          -> notFound -> 404 to the log viewer
#
# mtime is the age signal. A file's mtime is its last append, so a still-
# streaming run is never old enough to be selected; the stale-run watchdog
# terminates runs orders of magnitude sooner than this horizon.

set -uo pipefail

BASE="${PAPERCLIP_RUN_LOG_BASE:-/var/paperclip-offload/run-logs}"
RETENTION_DAYS="${PAPERCLIP_RUN_LOG_RETENTION_DAYS:-30}"
LOG="${PAPERCLIP_RUN_LOG_RETENTION_LOG:-/home/beai-agent/.paperclip/instances/default/logs/run-log-retention.log}"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
say() { echo "$(ts) $*" | tee -a "$LOG"; }

# Refuse to operate on anything that is not the real store. A missing or
# unexpected base path must fail loudly rather than sweep the wrong tree.
if [ ! -d "$BASE" ]; then
  say "FATAL base path not a directory: $BASE"
  exit 1
fi
case "$BASE" in
  /var/paperclip-offload/run-logs|/home/beai-agent/.paperclip/instances/*/data/run-logs) ;;
  *) say "FATAL refusing to sweep unrecognised base path: $BASE"; exit 1 ;;
esac

mkdir -p "$(dirname "$LOG")"

# -xdev keeps the sweep on one filesystem; -type f excludes symlinks entirely,
# so nothing outside the store can be reached through a planted link.
mapfile -t VICTIMS < <(find "$BASE" -xdev -type f -name '*.ndjson' -mtime "+${RETENTION_DAYS}" -print 2>/dev/null)

COUNT=${#VICTIMS[@]}
BYTES=0
if [ "$COUNT" -gt 0 ]; then
  BYTES=$(printf '%s\0' "${VICTIMS[@]}" | du -c --files0-from=- -b 2>/dev/null | tail -1 | cut -f1)
  BYTES=${BYTES:-0}
fi

if [ "$DRY_RUN" -eq 1 ]; then
  say "DRY-RUN horizon=${RETENTION_DAYS}d base=$BASE would_delete=${COUNT} bytes=${BYTES}"
  exit 0
fi

if [ "$COUNT" -eq 0 ]; then
  say "sweep horizon=${RETENTION_DAYS}d base=$BASE deleted=0 bytes=0 (nothing aged out)"
  exit 0
fi

FAILED=0
for f in "${VICTIMS[@]}"; do
  rm -f -- "$f" || FAILED=$((FAILED+1))
done

# Drop the now-empty companyId/agentId shells left behind. -mindepth 1 protects
# the store root itself.
find "$BASE" -xdev -mindepth 1 -type d -empty -delete 2>/dev/null

REMAIN=$(find "$BASE" -xdev -type f -name '*.ndjson' 2>/dev/null | wc -l)
say "sweep horizon=${RETENTION_DAYS}d base=$BASE deleted=$((COUNT-FAILED)) failed=${FAILED} bytes=${BYTES} remaining_files=${REMAIN}"
exit 0
