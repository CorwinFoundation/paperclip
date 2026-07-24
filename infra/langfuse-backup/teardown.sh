#!/bin/sh
set -eu

COMPOSE_FILE=${1:-infra/langfuse-backup/docker-compose.yml}
ENV_FILE=${2:-/etc/backbond/langfuse-synthetic.env}

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" down --volumes --remove-orphans
echo "BEAAA-17701 teardown complete; revoke every referenced Paperclip secret version."
