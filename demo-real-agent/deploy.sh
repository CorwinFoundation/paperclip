#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${1:?usage: deploy.sh SOURCE_DIR}"
ENV_FILE="/etc/backbond/real-agent-demo.env"
PREFLIGHT_HELPER="/usr/local/sbin/backbond-real-agent-demo-secret-preflight"
CONTAINER="backbond-passive-monitor-demo"
CANDIDATE="${CONTAINER}-candidate"
ROLLBACK="${CONTAINER}-rollback"
IMAGE="backbond/real-agent-demo:${DEMO_GIT_SHA:?DEMO_GIT_SHA is required}"
RUN_VOLUME="backbond-real-agent-demo-runs"
PROD_PORT="8765"
CANDIDATE_PORT="${CANDIDATE_PORT:-18765}"
JUDGE_SOCKET_DIR="/run/backbond-claude-judge"
JUDGE_SOCKET_HOST="${JUDGE_SOCKET_DIR}/judge.sock"
CLAUDE_PROXY_ENABLED=false

parse_judge_socket_status() {
  local output="$1"
  local -a status_lines=()
  mapfile -t status_lines < <(grep '^JUDGE_SOCKET:' <<<"$output" || true)
  if (( ${#status_lines[@]} != 1 )); then
    return 2
  fi
  case "${status_lines[0]}" in
    'JUDGE_SOCKET: ready') printf 'ready\n' ;;
    'JUDGE_SOCKET: missing') printf 'missing\n' ;;
    *) return 2 ;;
  esac
}

if [[ -n "${CLAUDE_JUDGE_SOCKET_DIR+x}" && "$CLAUDE_JUDGE_SOCKET_DIR" != "$JUDGE_SOCKET_DIR" ]]; then
  echo "CLAUDE_JUDGE_SOCKET_DIR must not override the fixed judge socket directory" >&2
  exit 1
fi
if [[ ! -x "$PREFLIGHT_HELPER" ]]; then
  echo "fixed secret preflight helper is not installed" >&2
  exit 1
fi
if ! PREFLIGHT_STATUS="$(sudo --non-interactive "$PREFLIGHT_HELPER")"; then
  echo "secret preflight evaluation failed" >&2
  exit 1
fi
printf '%s\n' "$PREFLIGHT_STATUS"
if grep -Fxq 'DEMO_JUDGE_BACKEND: configured' <<<"$PREFLIGHT_STATUS"; then
  CLAUDE_PROXY_ENABLED=true
  if ! JUDGE_SOCKET_STATUS="$(parse_judge_socket_status "$PREFLIGHT_STATUS")"; then
    echo "secret preflight returned malformed judge-socket status" >&2
    exit 1
  fi
  if [[ "$JUDGE_SOCKET_STATUS" == missing ]]; then
    echo "Claude judge socket is not ready: $JUDGE_SOCKET_HOST" >&2
    exit 1
  fi
fi

container_args() {
  local args=(
    --restart unless-stopped
    --read-only
    --tmpfs /tmp:rw,noexec,nosuid,size=64m
    --cap-drop ALL
    --security-opt no-new-privileges
    --user 10001:10001
    --mount "source=${RUN_VOLUME},target=/data/runs"
    --mount "type=bind,source=${ENV_FILE},target=/etc/backbond/real-agent-demo.env,readonly"
    -e "DEMO_ENVIRONMENT=public-demo"
    -e "DEMO_WORKFLOW=${DEMO_WORKFLOW:-Real Agent 203-Case Demo}"
    -e "DEMO_GIT_SHA=${DEMO_GIT_SHA}"
    -e "DEMO_DEPLOYED_AT=${DEMO_DEPLOYED_AT:-unknown}"
    -e "ARE_FULL_SCAN_MAX_TARGET_CALLS=246"
    -e "ARE_FULL_SCAN_MAX_JUDGE_CALLS=203"
    -e "ARE_FULL_SCAN_MAX_JUDGE_COST_USD=100"
    -e "ARE_FULL_SCAN_MAX_WALL_CLOCK_SECONDS=28800"
  )
  if [[ "$CLAUDE_PROXY_ENABLED" == true ]]; then
    args+=(
      --mount "type=bind,source=${JUDGE_SOCKET_DIR},target=/run/claude-judge,readonly"
      -e "CLAUDE_JUDGE_SOCKET=/run/claude-judge/judge.sock"
    )
  fi
  printf '%s\n' "${args[@]}"
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
if ! run_instance "$CANDIDATE" "$CANDIDATE_PORT" "$IMAGE"; then
  docker rm -f "$CANDIDATE" >/dev/null 2>&1 || true
  echo "candidate failed to start on 127.0.0.1:${CANDIDATE_PORT}" >&2
  exit 1
fi
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
echo "Full 203-case acceptance still required: run smoke_test.py with --execute"
