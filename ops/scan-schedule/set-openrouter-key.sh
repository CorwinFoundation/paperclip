#!/bin/sh
# BEAAA-39088: replace OPENROUTER_API_KEY in the scan-schedule env file.
#
# On 2026-10-08 the zoo lane aborted every target on
#   consecutive_upstream_quota:upstream_http_401:8
# with routing.openrouter_key_sha256_prefix 0229bc731728, while the working
# fleet key hashes to 525d002d4aea. The env file is root-only, so this is the
# one root step. It is idempotent: it rewrites only that line, keeps every
# other line, mode and owner, and prints the new key's sha256 prefix, never
# the key.
#
# Usage (as root):  set-openrouter-key.sh <file-holding-the-key>
set -eu
env_file=${BACKBOND_SCAN_ENV_FILE:-/etc/backbond/scan-schedule.env}
key_file=${1:?usage: set-openrouter-key.sh <file-holding-the-key>}
key=$(tr -d ' \t\r\n' < "$key_file")
case "$key" in
  sk-or-*) ;;
  *) echo "refusing: $key_file does not hold an OpenRouter key (sk-or-...)" >&2; exit 2 ;;
esac
[ -f "$env_file" ] || { echo "refusing: $env_file does not exist" >&2; exit 2; }
tmp=$(mktemp "$env_file.XXXXXX")
trap 'rm -f "$tmp"' EXIT
grep -v '^OPENROUTER_API_KEY=' "$env_file" > "$tmp" || true
printf 'OPENROUTER_API_KEY=%s\n' "$key" >> "$tmp"
chmod --reference="$env_file" "$tmp"
chown --reference="$env_file" "$tmp" 2>/dev/null || true
mv "$tmp" "$env_file"
trap - EXIT
printf 'OPENROUTER_API_KEY sha256 prefix now %s in %s\n' \
  "$(printf %s "$key" | sha256sum | cut -c1-12)" "$env_file"
