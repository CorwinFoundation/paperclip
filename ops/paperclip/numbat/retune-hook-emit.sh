#!/usr/bin/env bash
# BEAAA-33250 — cut the Numbat v4 hook's per-invocation cost.
#
# The 741 ms p50 measured on BEAAA-33227 is not process start and not the
# append. It is the embedded rule set being loaded and CEL-compiled on every
# exec, which only happens because the wrappers pass `--emit all`. Emitting
# events alone skips the detection pipeline: 15 ms p50 / 19 ms p90 (n=60).
#
# Every event still lands in the spool. What stops being written live is the
# derived finding/indicator/enforcement rows, and those are recomputable from
# the retained events:
#   numbat rules test --fixture <spool>.ndjson
# returns the same rule ids against the same event_id. Matchers in
# /etc/codex/requirements.toml are deliberately left alone, so nothing the T4
# isolation or continuous-capture checks read is dropped.
#
# Run as root. Idempotent: a second run reports "already tuned" and exits 0.
#   sudo /path/to/retune-hook-emit.sh            # apply
#   sudo /path/to/retune-hook-emit.sh --rollback # restore --emit all
set -euo pipefail

MANAGED_DIR=/usr/local/libexec/numbat-paperclip
IDENTITIES=(cto auditor scanner-engineer-2)
BACKUP_SUFFIX=.beaaa-33250.bak
MODE=apply
[ "${1:-}" = "--rollback" ] && MODE=rollback

[ "$(id -u)" = "0" ] || { echo "must run as root: $MANAGED_DIR is root-owned" >&2; exit 1; }
[ -d "$MANAGED_DIR" ] || { echo "no managed dir at $MANAGED_DIR" >&2; exit 1; }

if [ "$MODE" = rollback ]; then
  from="--emit events"; to="--emit all"
else
  from="--emit all"; to="--emit events"
fi

changed=0
for id in "${IDENTITIES[@]}"; do
  f="$MANAGED_DIR/$id"
  [ -f "$f" ] || { echo "skip $id: no wrapper"; continue; }
  grep -q 'numbat hook' "$f" || { echo "refusing $id: not a numbat wrapper" >&2; exit 1; }
  if ! grep -qF -- "$from" "$f"; then
    echo "already tuned: $id"
    continue
  fi
  [ -f "$f$BACKUP_SUFFIX" ] || cp -p "$f" "$f$BACKUP_SUFFIX"
  tmp=$(mktemp "$MANAGED_DIR/.$id.XXXXXX")
  sed "s|$from|$to|" "$f" > "$tmp"
  chmod --reference="$f" "$tmp"
  chown --reference="$f" "$tmp"
  mv -f "$tmp" "$f"
  echo "retuned: $id"
  changed=$((changed + 1))
done

echo
echo "re-pinned SHA-256 (record these alongside the four pinned on BEAAA-21399):"
sha256sum /etc/codex/requirements.toml "$MANAGED_DIR"/cto "$MANAGED_DIR"/auditor "$MANAGED_DIR"/scanner-engineer-2
echo
echo "changed $changed wrapper(s); requirements.toml and /usr/local/bin/numbat are untouched"
