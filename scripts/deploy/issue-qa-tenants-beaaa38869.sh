#!/usr/bin/env bash
# BEAAA-38869: keep two 7-day test tenant keys (qa-38869-a, qa-38869-b) live on
# the production scanner so the tenant-isolation check can run there.
# BEAAA-42359: the earlier version skipped any tenant already listed in the
# tokens file, so an expired or revoked key could never be renewed. Renewal now
# lives in the sudo-authorized deploy helper (`scanner-deploy qa-tenant-keys
# beaaa38869`): a key with at least a day left is kept, anything else is
# replaced by a fresh seven-day key and the old key_id revoked. Safe to rerun.
# Tokens land 0600 in are-deploy's home and are never written to stdout.
set -euo pipefail
HELPER="${SCANNER_DEPLOY_HELPER:-/usr/local/sbin/scanner-deploy}"
if [ "$(id -u)" = 0 ]; then
  exec "$HELPER" qa-tenant-keys beaaa38869
fi
exec sudo -n "$HELPER" qa-tenant-keys beaaa38869
