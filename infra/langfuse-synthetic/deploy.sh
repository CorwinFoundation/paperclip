#!/usr/bin/env bash
set -euo pipefail

: "${LANGFUSE_ENV_FILE:?root-readable environment file materialized from Paperclip secret references}"
if [[ ! -f "${LANGFUSE_ENV_FILE}" ]]; then
  echo "LANGFUSE_ENV_FILE does not exist" >&2
  exit 1
fi
owner_uid="$(stat -c '%u' "${LANGFUSE_ENV_FILE}")"
if [[ "${owner_uid}" != "0" ]]; then
  echo "LANGFUSE_ENV_FILE must be owned by root, got uid ${owner_uid}" >&2
  exit 1
fi
mode="$(stat -c '%a' "${LANGFUSE_ENV_FILE}")"
if [[ "${mode}" != "600" && "${mode}" != "400" ]]; then
  echo "LANGFUSE_ENV_FILE must have mode 0600 or 0400, got ${mode}" >&2
  exit 1
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker compose --env-file "${LANGFUSE_ENV_FILE}" -f "${here}/docker-compose.yml" config --quiet
docker compose --env-file "${LANGFUSE_ENV_FILE}" -f "${here}/docker-compose.yml" pull
docker compose --env-file "${LANGFUSE_ENV_FILE}" -f "${here}/docker-compose.yml" up -d
