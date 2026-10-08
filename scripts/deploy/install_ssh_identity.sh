#!/usr/bin/env bash
set -euo pipefail

deploy_key=${1:?deploy key path required}
known_hosts=${2:?known-hosts path required}

normalized_key=${DEPLOY_SSH_PRIVATE_KEY//$'\r'/}
normalized_key=${normalized_key//\\n/$'\n'}
normalized_hosts=${DEPLOY_SSH_KNOWN_HOSTS//$'\r'/}
normalized_hosts=${normalized_hosts//\\n/$'\n'}

if [[ "$normalized_key" == *"-----BEGIN "*" PRIVATE KEY-----"* ]]; then
  printf '%s\n' "$normalized_key" > "$deploy_key"
else
  encoded_key=${normalized_key//$'\n'/}
  if ! printf '%s' "$encoded_key" | base64 --decode > "$deploy_key" 2>/dev/null; then
    echo "deploy SSH identity is neither a private-key document nor strict base64" >&2
    exit 1
  fi
  tr -d '\r' < "$deploy_key" > "${deploy_key}.normalized"
  mv "${deploy_key}.normalized" "$deploy_key"
  printf '\n' >> "$deploy_key"
fi

printf '%s\n' "$normalized_hosts" > "$known_hosts"
chmod 0600 "$deploy_key" "$known_hosts"
