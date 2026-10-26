#!/usr/bin/env bash
# Operator-controlled Linux x86_64 worker. No AWS assets are created here.
set -euo pipefail
umask 077
: "${QUICK_PILOT_ROOT:?set the absolute private customer directory}"
: "${QUICK_PILOT_IMAGE:?set the reviewed image tag or digest}"
root=$(realpath -- "$QUICK_PILOT_ROOT")
[[ "$root" != / && -d "$root/config" && -d "$root/state" && -d "$root/evidence" ]]
[[ -f "$root/config/runtime.env" && -d /usr/local/aws-cli ]]
if [[ $# -eq 0 ]]; then
  set -- tools.quick_pilot_operations runtime-check
fi
# Host networking allows IMDSv2 credential_source=Ec2InstanceMetadata profiles.
# No listener or inbound mapping is started. Keep the host SG inbound empty.
exec docker run --rm --network host --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges --shm-size=256m \
  --tmpfs /home/scanner:rw,nosuid,nodev,size=64m,uid=10001,gid=10001,mode=0700 \
  --tmpfs /tmp:rw,nosuid,size=512m --env-file "$root/config/runtime.env" \
  --env ARE_AMAZON_QUICK_ENABLED=1 \
  --env ARE_AMAZON_QUICK_TARGETS_FILE=/pilot/config/targets.json \
  --env ARE_AMAZON_QUICK_STATE_DIR=/pilot/state \
  --env AWS_CONFIG_FILE=/pilot/config/aws-config \
  --env AWS_SHARED_CREDENTIALS_FILE=/pilot/config/no-static-credentials \
  --mount "type=bind,src=$root/config,dst=/pilot/config,readonly" \
  --mount "type=bind,src=$root/state,dst=/pilot/state" \
  --mount "type=bind,src=$root/evidence,dst=/pilot/evidence" \
  --mount type=bind,src=/usr/local/aws-cli,dst=/usr/local/aws-cli,readonly \
  "$QUICK_PILOT_IMAGE" "$@"
