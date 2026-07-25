#!/usr/bin/env bash
set -euo pipefail

: "${LANGFUSE_ENV_FILE:?root-readable environment file materialized from Paperclip secret references}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
receipt="${1:-${here}/teardown-receipt.json}"

docker compose --env-file "${LANGFUSE_ENV_FILE}" -f "${here}/docker-compose.yml" down --volumes --remove-orphans

jq -n \
  --arg issue "BEAAA-17702" \
  --arg completed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg action "dedicated Langfuse containers, network, and volumes removed" \
  '{
    issue:$issue,
    completed_at:$completed_at,
    action:$action,
    credential_action_required:"revoke every secret ID listed for BEAAA-17702 in Paperclip"
  }' >"${receipt}"
chmod 0644 "${receipt}"
