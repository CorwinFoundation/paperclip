#!/usr/bin/env bash
# BEAAA-13362 — box-side deploy verb (Option A, ADR-0057 / ADR BEAAA-13360).
#
# This is the ONLY thing the self-hosted `production` runner (`are-deploy`
# user) is permitted to run under sudo. It contains all the privileged docker
# mutation; the runner has no other root capability. Install to
# /usr/local/sbin/box_deploy (root:root, 0755) and whitelist in sudoers:
#
#   are-deploy ALL=(root) NOPASSWD: /usr/local/sbin/box_deploy
#
# Verbs (the interface cd.yml depends on — keep stable):
#   current            print the image ref of the running scanner container ('' if none)
#   pull    <ref>      docker pull <ref>            (no swap)
#   activate <ref>     swap the running container to <ref>, keep prior image on box
#   rollback <ref>     restart the container on the previously-active <ref>
#
# INVARIANTS
#   * State + secrets are host-mounted, never baked into the image
#     (/var/lib/are-scanner/, secrets env-file) — see ADR-0057 + Phase 1.
#   * The previous image is NEVER pruned here, so rollback is a local retag +
#     restart measured in seconds.
#   * nginx/TLS termination is unchanged; only the app container is swapped.
set -euo pipefail

# ---- box-local configuration (override via /etc/are-scanner/deploy.env) -----
CONTAINER="${ARE_CONTAINER_NAME:-are-scanner-edge}"
STATE_DIR="${ARE_STATE_DIR:-/var/lib/are-scanner}"
SECRETS_ENV="${ARE_SECRETS_ENV:-/etc/are-scanner/secrets.env}"
# Where the app binds today (FastAPI/uvicorn behind nginx). Phase 1's compose
# unit pins this; kept identical here so the swap is transparent to nginx.
BIND="${ARE_BIND:-127.0.0.1:8080}"
# shellcheck disable=SC1090
[ -f /etc/are-scanner/deploy.env ] && . /etc/are-scanner/deploy.env

log() { echo "[box_deploy] $*" >&2; }

current() {
  docker inspect --format '{{ index .Config.Image }}' "${CONTAINER}" 2>/dev/null || true
}

pull() {
  local ref="$1"
  [ -n "${ref}" ] || { log "pull: missing ref"; exit 2; }
  log "pull ${ref}"
  docker pull "${ref}"
}

run_container() {
  # Start CONTAINER on the given ref with the standard host mounts + bind.
  local ref="$1"
  docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
  local host="${BIND%%:*}" port="${BIND##*:}"
  docker run -d --name "${CONTAINER}" \
    --restart unless-stopped \
    -p "${host}:${port}:8080" \
    -v "${STATE_DIR}:/var/lib/are-scanner" \
    ${SECRETS_ENV:+--env-file "${SECRETS_ENV}"} \
    "${ref}"
}

activate() {
  local ref="$1"
  [ -n "${ref}" ] || { log "activate: missing ref"; exit 2; }
  log "activate ${ref} (prev: $(current || echo none))"
  # ADR-0057: ScanLog._migrate() (PR #51) forward-migrates additive columns on
  # connect, so pointing a newer image at the same state dir is safe.
  run_container "${ref}"
  log "activated ${ref}"
}

rollback() {
  local ref="$1"
  [ -n "${ref}" ] || { log "rollback: no previous ref supplied"; exit 3; }
  log "ROLLBACK -> ${ref}"
  # Previous image is retained on the box, so this is retag+restart, seconds.
  docker image inspect "${ref}" >/dev/null 2>&1 || docker pull "${ref}"
  run_container "${ref}"
  log "rolled back to ${ref}"
}

case "${1:-}" in
  current)  current ;;
  pull)     pull "${2:-}" ;;
  activate) activate "${2:-}" ;;
  rollback) rollback "${2:-}" ;;
  *) echo "usage: box_deploy {current|pull <ref>|activate <ref>|rollback <ref>}" >&2; exit 64 ;;
esac
