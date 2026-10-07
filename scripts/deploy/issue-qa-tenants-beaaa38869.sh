#!/usr/bin/env bash
# BEAAA-38869: issue two 7-day test tenant keys (qa-38869-a, qa-38869-b) on the
# live scanner so the tenant-isolation check can run against production.
# Run as root on srv1749248. Safe to rerun: a tenant already listed in the
# tokens file is skipped. Tokens land 0600 in are-deploy's home, never on stdout.
set -euo pipefail
C="${SCANNER_CONTAINER:-backbond-scanner-live-edge}"
OUT="${QA_TOKENS_FILE:-/home/are-deploy/qa-38869.tokens}"
OWNER="${QA_TOKENS_OWNER:-are-deploy}"

docker exec "$C" sh -c 'test -n "${ARE_KEY_STORE_PATH:-}"' \
  || { echo "ARE_KEY_STORE_PATH is not set in $C; edge gateway key store not in use" >&2; exit 1; }

umask 077
touch "$OUT"
chown "$OWNER:$OWNER" "$OUT"
chmod 600 "$OUT"

for t in qa-38869-a qa-38869-b; do
  if grep -q "^$t " "$OUT"; then
    echo "$t already issued"
    continue
  fi
  k="bbqa_$(openssl rand -hex 24)"
  docker exec "$C" sh -c 'python -m api.edge_gateway --store "$ARE_KEY_STORE_PATH" issue \
      --token "$1" --key-id "$2" --tenant-id "$2" --ttl-seconds 604800' _ "$k" "$t"
  echo "$t $k" >>"$OUT"
done
echo "tokens in $OUT"
