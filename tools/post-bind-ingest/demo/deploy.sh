#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${1:?usage: deploy.sh SOURCE_DIR}"
CONTAINER="backbond-passive-monitor-demo"
IMAGE="backbond/passive-monitor-demo:${DEMO_GIT_SHA:?DEMO_GIT_SHA is required}"
CANDIDATE="${CONTAINER}-candidate"
RUN_VOLUME="backbond-passive-demo-runs"
PROD_PORT="8765"
CANDIDATE_PORT="18765"
PREVIOUS_IMAGE="$(docker inspect --format '{{.Config.Image}}' "$CONTAINER" 2>/dev/null || true)"

container_args() {
  printf '%s\n' \
    --restart unless-stopped \
    --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --user 10001:10001 \
    --mount "source=${RUN_VOLUME},target=/data/runs" \
    -e "DEMO_ENVIRONMENT=public-demo" \
    -e "DEMO_WORKFLOW=${DEMO_WORKFLOW:-Passive Monitor Demo}" \
    -e "DEMO_GIT_SHA=${DEMO_GIT_SHA}" \
    -e "DEMO_GIT_REF=${DEMO_GIT_REF:-unknown}" \
    -e "DEMO_GITHUB_REPOSITORY=${DEMO_GITHUB_REPOSITORY:-CorwinFoundation/are-scanner}" \
    -e "DEMO_GITHUB_RUN_ID=${DEMO_GITHUB_RUN_ID:-manual}" \
    -e "DEMO_GITHUB_RUN_URL=${DEMO_GITHUB_RUN_URL:-}" \
    -e "DEMO_DEPLOYED_AT=${DEMO_DEPLOYED_AT:-unknown}"
}

wait_for_health() {
  local port="$1"
  local attempts="${2:-40}"
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
docker build --pull -f "$SOURCE_DIR/tools/post-bind-ingest/demo/Dockerfile.demo" -t "$IMAGE" "$SOURCE_DIR"
docker volume create "$RUN_VOLUME" >/dev/null

docker rm -f "$CANDIDATE" >/dev/null 2>&1 || true
run_instance "$CANDIDATE" "$CANDIDATE_PORT" "$IMAGE"
if ! wait_for_health "$CANDIDATE_PORT" 60; then
  docker logs "$CANDIDATE" --tail 200 || true
  docker rm -f "$CANDIDATE" >/dev/null 2>&1 || true
  echo "Candidate health check failed" >&2
  exit 1
fi
if ! python3 "$SOURCE_DIR/tools/post-bind-ingest/demo/smoke_test.py" \
  --url "http://127.0.0.1:${CANDIDATE_PORT}" --timeout 180; then
  docker logs "$CANDIDATE" --tail 200 || true
  docker rm -f "$CANDIDATE" >/dev/null 2>&1 || true
  echo "Candidate proof run failed" >&2
  exit 1
fi
docker rm -f "$CANDIDATE" >/dev/null

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
run_instance "$CONTAINER" "$PROD_PORT" "$IMAGE"
if ! wait_for_health "$PROD_PORT" 60; then
  docker logs "$CONTAINER" --tail 200 || true
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  if [[ -n "$PREVIOUS_IMAGE" ]]; then
    echo "Rolling back to ${PREVIOUS_IMAGE}" >&2
    run_instance "$CONTAINER" "$PROD_PORT" "$PREVIOUS_IMAGE"
    wait_for_health "$PROD_PORT" 60 || true
  fi
  echo "Production health check failed" >&2
  exit 1
fi

if command -v cloudflared >/dev/null 2>&1 && command -v systemctl >/dev/null 2>&1; then
  if [[ ! -f /etc/systemd/system/backbond-passive-demo-tunnel.service ]]; then
    cat >/etc/systemd/system/backbond-passive-demo-tunnel.service <<'UNIT'
[Unit]
Description=BackBond passive monitor demo quick tunnel
After=network-online.target docker.service
Requires=docker.service

[Service]
ExecStart=/usr/local/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8765
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable --now backbond-passive-demo-tunnel.service
  else
    systemctl start backbond-passive-demo-tunnel.service
  fi
fi

echo "Deployment health: PASS"
echo "Image: ${IMAGE}"
echo "Previous image: ${PREVIOUS_IMAGE:-none}"
