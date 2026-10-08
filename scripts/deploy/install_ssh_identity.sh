#!/usr/bin/env bash
set -euo pipefail

deploy_key=${1:?deploy key path required}
known_hosts=${2:?known-hosts path required}

normalized_key=${DEPLOY_SSH_PRIVATE_KEY//$'\r'/}
normalized_key=${normalized_key//\\n/$'\n'}
normalized_hosts=${DEPLOY_SSH_KNOWN_HOSTS//$'\r'/}
normalized_hosts=${normalized_hosts//\\n/$'\n'}

candidate="${deploy_key}.candidate"
cleanup() { rm -f "$candidate"; }
trap cleanup EXIT

try_candidate() {
  local value=$1
  printf '%s' "$value" | tr -d '\r' > "$candidate"
  printf '\n' >> "$candidate"
  chmod 0600 "$candidate"
  if ssh-keygen -y -f "$candidate" >/dev/null 2>&1; then
    mv "$candidate" "$deploy_key"
    return 0
  fi
  return 1
}

# Prefer the value as supplied after newline normalization. This handles raw
# OpenSSH/PEM keys and escaped-newline secrets without exposing key material.
if ! try_candidate "$normalized_key"; then
  # Some secret stores wrap the complete value in quotes. Remove only a
  # matching outer pair before trying the encoded forms.
  unquoted=$normalized_key
  if [[ "$unquoted" == \"*\" && "$unquoted" == *\" ]]; then
    unquoted=${unquoted:1:${#unquoted}-2}
  fi
  encoded_key=$(printf '%s' "$unquoted" | tr -d '[:space:]')

  # Try standard base64 first, then URL-safe base64. Validate the decoded
  # candidate before replacing the destination so partial decodes are rejected.
  if ! printf '%s' "$encoded_key" | base64 --decode > "$candidate" 2>/dev/null || ! try_candidate "$(cat "$candidate")"; then
    urlsafe=${encoded_key//-/+}
    urlsafe=${urlsafe//_/\/}
    case $(( ${#urlsafe} % 4 )) in
      2) urlsafe+='==' ;;
      3) urlsafe+='=' ;;
    esac
    if ! printf '%s' "$urlsafe" | base64 --decode > "$candidate" 2>/dev/null || ! try_candidate "$(cat "$candidate")"; then
      echo "deploy SSH identity is not a valid private-key document or base64 encoding" >&2
      exit 1
    fi
  fi
fi

printf '%s\n' "$normalized_hosts" > "$known_hosts"
chmod 0600 "$deploy_key" "$known_hosts"
