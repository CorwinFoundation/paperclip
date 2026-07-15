#!/usr/bin/env bash
# BEAAA-13362 — box-side deploy verb (operator-mediated PUSH, ADR-0057 v2.0 /
# ADR BEAAA-13360, corrected by BEAAA-13387).
#
# The box runs under default-deny EGRESS (BEAAA-13385): NO outbound HTTPS, so it
# can NOT `docker pull` its own release. The image is delivered as a tarball
# pushed IN over the operator's inbound SSH channel (docker/podman save on an
# egress-capable machine -> scp in). This script loads that tarball, RE-VERIFIES
# the digest on the box before activate, and contains all the privileged docker
# mutation; the operator has no other root capability. Install to
# /usr/local/sbin/box_deploy (root:root, 0755) and whitelist in sudoers:
#
#   are-deploy ALL=(root) NOPASSWD: /usr/local/sbin/box_deploy
#
# Verbs (the interface operator_push.sh depends on — keep stable):
#   current                 print the image ref of the running scanner container ('' if none)
#   load <tarball> <digest> docker/podman load the pushed-in tarball, then FAIL
#                           unless the loaded image's repo digest / config digest
#                           matches <digest> (on-box digest re-verify). No swap.
#   activate <ref>          swap the running container to <ref>, keep prior image on box
#   rollback <ref>          restart the container on the previously-active <ref>
#                           (retained locally; NO egress, NO pull)
#
# INVARIANTS
#   * The box NEVER originates an outbound connection here — no `docker pull`.
#     Delivery is a push (tarball in); rollback uses the locally-retained image.
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
# Container engine — docker or podman; the box may run either.
ENGINE="${ARE_CONTAINER_ENGINE:-docker}"
# Where the app binds today (FastAPI/uvicorn behind nginx). Phase 1's compose
# unit pins this; kept identical here so the swap is transparent to nginx.
BIND="${ARE_BIND:-127.0.0.1:8080}"
# shellcheck disable=SC1090
[ -f /etc/are-scanner/deploy.env ] && . /etc/are-scanner/deploy.env

log() { echo "[box_deploy] $*" >&2; }

current() {
  "${ENGINE}" inspect --format '{{ index .Config.Image }}' "${CONTAINER}" 2>/dev/null || true
}

# Load the pushed-in tarball and RE-VERIFY the digest on the box. This is the
# on-box half of "what the Founder approved == what activates": the transport
# (scp over operator SSH) is not trusted; the digest is.
load() {
  local tarball="$1" want_digest="$2"
  [ -n "${tarball}" ] || { log "load: missing tarball path"; exit 2; }
  [ -f "${tarball}" ] || { log "load: tarball not found: ${tarball}"; exit 2; }
  [ -n "${want_digest}" ] || { log "load: missing expected digest (on-box re-verify is mandatory)"; exit 2; }

  log "load ${tarball} (expect digest ${want_digest})"
  # `load` output names the loaded ref(s); capture for the digest lookup.
  local loaded_ref
  loaded_ref="$("${ENGINE}" load -i "${tarball}" 2>&1 | sed -n 's/^Loaded image: //p;s/^Loaded image ID: //p' | head -1)"
  [ -n "${loaded_ref}" ] || { log "load: could not determine loaded ref from ${ENGINE} load"; exit 4; }
  log "loaded ${loaded_ref}"

  # Re-verify: the loaded image's RepoDigests (or Id) must carry the approved
  # digest. Fail CLOSED on any mismatch — do NOT activate an unverified image.
  local got
  got="$("${ENGINE}" inspect --format '{{ join .RepoDigests " " }} {{ .Id }}' "${loaded_ref}" 2>/dev/null || true)"
  case "${got}" in
    *"${want_digest}"*)
      log "digest re-verify OK (${want_digest} present on ${loaded_ref})"
      # Emit the verified ref so the caller can activate exactly it.
      echo "${loaded_ref}"
      ;;
    *)
      log "DIGEST MISMATCH: expected ${want_digest}, image carries [${got}] — refusing to activate"
      exit 5
      ;;
  esac
}

run_container() {
  # Start CONTAINER on the given ref with the standard host mounts + bind.
  local ref="$1"
  "${ENGINE}" rm -f "${CONTAINER}" >/dev/null 2>&1 || true
  local host="${BIND%%:*}" port="${BIND##*:}"
  "${ENGINE}" run -d --name "${CONTAINER}" \
    --restart unless-stopped \
    -p "${host}:${port}:8080" \
    -v "${STATE_DIR}:/var/lib/are-scanner" \
    ${SECRETS_ENV:+--env-file "${SECRETS_ENV}"} \
    "${ref}"
}

activate() {
  local ref="$1"
  [ -n "${ref}" ] || { log "activate: missing ref"; exit 2; }
  # The ref MUST already be present locally (loaded via `load`); the box has no
  # egress to fetch it. Fail closed rather than attempt a (doomed) pull.
  "${ENGINE}" image inspect "${ref}" >/dev/null 2>&1 || {
    log "activate: image ${ref} not present locally — did you 'load' it first? (box has no egress)"; exit 6;
  }
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
  # Previous image is retained on the box; NO egress, NO pull. If it is somehow
  # absent locally, we cannot fetch it — fail closed and page the operator.
  "${ENGINE}" image inspect "${ref}" >/dev/null 2>&1 || {
    log "rollback: previous image ${ref} not present locally and box has no egress — cannot self-heal, paging operator"; exit 7;
  }
  run_container "${ref}"
  log "rolled back to ${ref}"
}

case "${1:-}" in
  current)  current ;;
  load)     load "${2:-}" "${3:-}" ;;
  activate) activate "${2:-}" ;;
  rollback) rollback "${2:-}" ;;
  *) echo "usage: box_deploy {current|load <tarball> <digest>|activate <ref>|rollback <ref>}" >&2; exit 64 ;;
esac
