#!/usr/bin/env bash
set -euo pipefail

deploy_key=${1:?deploy key path required}
known_hosts=${2:?known-hosts path required}

normalized_key=${DEPLOY_SSH_PRIVATE_KEY//$'\r'/}
normalized_key=${normalized_key//\\n/$'\n'}
normalized_hosts=${DEPLOY_SSH_KNOWN_HOSTS//$'\r'/}
normalized_hosts=${normalized_hosts//\\n/$'\n'}

candidate="${deploy_key}.candidate"
converted="${deploy_key}.converted"
cleanup() { rm -f "$candidate" "$converted"; }
trap cleanup EXIT

accept_candidate() {
  local path=$1
  chmod 0600 "$path"
  if ssh-keygen -y -f "$path" >/dev/null 2>&1; then
    mv "$path" "$deploy_key"
    return 0
  fi
  return 1
}

try_value() {
  printf '%s\n' "$1" | tr -d '\r' > "$candidate"
  accept_candidate "$candidate"
}

try_decoded_candidate() {
  # Keep decoded bytes file-backed: Bash variables cannot preserve NUL bytes.
  if accept_candidate "$candidate"; then
    return 0
  fi
  if head -c 15 "$candidate" | cmp -s - <(printf 'openssh-key-v1\0'); then
    {
      printf '%s\n' '-----BEGIN OPENSSH PRIVATE KEY-----'
      base64 -w 70 "$candidate"
      printf '%s\n' '-----END OPENSSH PRIVATE KEY-----'
    } > "$converted"
    accept_candidate "$converted" && return 0
  fi
  if openssl pkey -inform DER -in "$candidate" -out "$converted" >/dev/null 2>&1 \
      && accept_candidate "$converted"; then
    return 0
  fi
  if openssl rsa -inform DER -in "$candidate" -out "$converted" >/dev/null 2>&1 \
      && accept_candidate "$converted"; then
    return 0
  fi
  return 1
}

# Prefer the value as supplied after newline normalization. This handles raw
# OpenSSH/PEM keys and escaped-newline secrets without exposing key material.
if ! try_value "$normalized_key"; then
  # Some secret stores wrap the complete value in quotes. Remove only a
  # matching outer pair before trying the encoded forms.
  unquoted=$normalized_key
  if [[ "$unquoted" == \"*\" && "$unquoted" == *\" ]]; then
    unquoted=${unquoted:1:${#unquoted}-2}
  fi
  encoded_key=$(printf '%s' "$unquoted" | tr -d '[:space:]')

  # Try standard base64 first, then URL-safe base64. Validate the decoded
  # candidate before replacing the destination so partial decodes are rejected.
  if ! printf '%s' "$encoded_key" | base64 --decode > "$candidate" 2>/dev/null || ! try_decoded_candidate; then
    urlsafe=${encoded_key//-/+}
    urlsafe=${urlsafe//_/\/}
    case $(( ${#urlsafe} % 4 )) in
      2) urlsafe+='==' ;;
      3) urlsafe+='=' ;;
    esac
    if ! printf '%s' "$urlsafe" | base64 --decode > "$candidate" 2>/dev/null || ! try_decoded_candidate; then
      echo "deploy SSH identity is not a valid private-key document or base64 encoding" >&2
      exit 1
    fi
  fi
fi

printf '%s\n' "$normalized_hosts" > "$known_hosts"
chmod 0600 "$deploy_key" "$known_hosts"
