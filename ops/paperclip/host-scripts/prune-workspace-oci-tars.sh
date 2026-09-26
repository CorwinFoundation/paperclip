#!/usr/bin/env bash
# prune-workspace-oci-tars.sh [--dry-run]
#
# Deletes *.oci.tar build/delivery artifacts older than RETENTION_DAYS from
# Paperclip agent workspace dirs on sda (/mnt/paperclipdata). These recur on
# every scanner delivery (~922 MB each) and are redundant once delivered — the
# image is uploaded as OCI-PARTS attachments and its sha256 is recorded in the
# delivery dir, so the local tarball can be regenerated/re-fetched.
#
# Age-based (mtime) so an in-progress build (fresh file) is never touched.
# Only ever removes files matching *.oci.tar under $WORKSPACES.
#
# See memory: backup-prune-cron, paperclip-cache-offload.
set -euo pipefail

WORKSPACES="${WORKSPACES:-/mnt/paperclipdata/dot-paperclip/instances/default/workspaces}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
LOG="${LOG:-/home/beai-agent/.paperclip-oci-prune.log}"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }

if [ ! -d "$WORKSPACES" ]; then
  echo "$(ts) workspaces dir missing: $WORKSPACES" >>"$LOG"
  exit 0
fi

mapfile -d '' FILES < <(find "$WORKSPACES" -type f -name '*.oci.tar' -mtime "+$RETENTION_DAYS" -print0 2>/dev/null)

total=0; count=0
for f in "${FILES[@]:-}"; do
  [ -n "$f" ] || continue
  sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
  if [ "$DRY" = 1 ]; then
    echo "$(ts) DRY would delete (${sz}B) $f" >>"$LOG"
  else
    rm -f -- "$f" && echo "$(ts) deleted (${sz}B) $f" >>"$LOG"
  fi
  total=$((total + sz)); count=$((count + 1))
done

suffix=""; [ "$DRY" = 1 ] && suffix=" (dry-run)"
echo "$(ts) done: ${count} file(s), $((total / 1024 / 1024)) MiB${suffix} (retention ${RETENTION_DAYS}d)" >>"$LOG"
