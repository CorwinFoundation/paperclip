#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${1:?usage: deploy.sh SOURCE_DIR}"
ENV_FILE="${REAL_AGENT_DEMO_ENV_FILE:-/etc/backbond/real-agent-demo.env}"
CONTAINER="backbond-passive-monitor-demo"
CANDIDATE="${CONTAINER}-candidate"
ROLLBACK="${CONTAINER}-rollback"
IMAGE="backbond/real-agent-demo:${DEMO_GIT_SHA:?DEMO_GIT_SHA is required}"
RUN_VOLUME="backbond-real-agent-demo-runs"
PROD_PORT="8765"
CANDIDATE_PORT="18765"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing $ENV_FILE; configure demo access, signing, and judge credentials" >&2
  exit 1
fi
if [[ "$(stat -c '%a' "$ENV_FILE")" != "600" ]]; then
  echo "$ENV_FILE must be mode 600" >&2
  exit 1
fi
if [[ "$(stat -c '%u' "$ENV_FILE")" != "10001" ]]; then
  echo "$ENV_FILE must be owned by uid 10001 for the unprivileged container" >&2
  exit 1
fi

require_env_name() {
  local name="$1"
  if ! grep -Eq "^${name}=.+" "$ENV_FILE"; then
    echo "$ENV_FILE is missing $name" >&2
    exit 1
  fi
}

require_env_name DEMO_ACCESS_TOKEN
require_env_name ARE_BUNDLE_SIGNING_KEY
if ! grep -Eq '^(OPENROUTER_API_KEY|ANTHROPIC_API_KEY)=.+' "$ENV_FILE"; then
  echo "$ENV_FILE requires OPENROUTER_API_KEY or ANTHROPIC_API_KEY" >&2
  exit 1
fi

container_args() {
  printf '%s\n' \
    --restart unless-stopped \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --user 10001:10001 \
    --mount "source=${RUN_VOLUME},target=/data/runs" \
    --mount "type=bind,source=${ENV_FILE},target=/etc/backbond/real-agent-demo.env,readonly" \
    -e "DEMO_ENVIRONMENT=public-demo" \
    -e "DEMO_WORKFLOW=${DEMO_WORKFLOW:-Real Agent 90-Case Demo}" \
    -e "DEMO_GIT_SHA=${DEMO_GIT_SHA}" \
    -e "DEMO_DEPLOYED_AT=${DEMO_DEPLOYED_AT:-unknown}" \
    -e "ARE_FULL_SCAN_MAX_TARGET_CALLS=110" \
    -e "ARE_FULL_SCAN_MAX_JUDGE_CALLS=90" \
    -e "ARE_FULL_SCAN_MAX_JUDGE_COST_USD=100" \
    -e "ARE_FULL_SCAN_MAX_WALL_CLOCK_SECONDS=10800"
}

wait_for_health() {
  local port="$1"
  local attempts="${2:-60}"
  for _ in $(seq 1 "$attempts"); do
    if curl --fail --silent --max-time 3 "http://127.0.0.1:${port}/api/health" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

run_instance() {
  local name="$1"
  local port="$2"
  local image="$3"
  mapfile -t args < <(container_args)
  docker run -d --name "$name" -p "127.0.0.1:${port}:8765" "${args[@]}" "$image" >/dev/null
}

echo "Building ${IMAGE}"
docker build --pull -f "$SOURCE_DIR/demo-real-agent/Dockerfile" -t "$IMAGE" "$SOURCE_DIR"
docker volume create "$RUN_VOLUME" >/dev/null

docker rm -f "$CANDIDATE" >/dev/null 2>&1 || true
run_instance "$CANDIDATE" "$CANDIDATE_PORT" "$IMAGE"
if ! wait_for_health "$CANDIDATE_PORT"; then
  docker logs "$CANDIDATE" --tail 200 || true
  docker rm -f "$CANDIDATE" >/dev/null 2>&1 || true
  echo "candidate health check failed" >&2
  exit 1
fi
python3 "$SOURCE_DIR/demo-real-agent/smoke_test.py" \
  --url "http://127.0.0.1:${CANDIDATE_PORT}" --health-only
docker rm -f "$CANDIDATE" >/dev/null

docker rm -f "$ROLLBACK" >/dev/null 2>&1 || true
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  docker stop "$CONTAINER" >/dev/null
  docker rename "$CONTAINER" "$ROLLBACK"
fi

if ! run_instance "$CONTAINER" "$PROD_PORT" "$IMAGE" || ! wait_for_health "$PROD_PORT"; then
  docker logs "$CONTAINER" --tail 200 || true
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  if docker inspect "$ROLLBACK" >/dev/null 2>&1; then
    echo "restoring the untouched previous container" >&2
    docker rename "$ROLLBACK" "$CONTAINER"
    docker start "$CONTAINER" >/dev/null
  fi
  exit 1
fi

docker rm -f "$ROLLBACK" >/dev/null 2>&1 || true
echo "Deployment health: PASS"
echo "Image: ${IMAGE}"
echo "Full 90-case acceptance still required: run smoke_test.py with --execute"
