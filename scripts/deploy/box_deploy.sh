#!/usr/bin/env bash
# BEAAA-13362 — box-side deploy verb (operator-mediated PUSH, ADR-0057 v2.0 /
# ADR BEAAA-13360, corrected by BEAAA-13387).
#
# The box runs under default-deny EGRESS (BEAAA-13385): NO outbound HTTPS, so it
# can NOT `docker pull` its own release. The image is delivered as a tarball
# pushed IN over the operator's inbound SSH channel. This script verifies and
# loads that tarball, then contains all privileged container/nginx mutation.
#
# Install to /usr/local/sbin/box_deploy (root:root, 0755) and whitelist:
#
#   are-deploy ALL=(root) NOPASSWD: /usr/local/sbin/box_deploy
#
# Verbs:
#   current                 print the running scanner container image ('' if none)
#   load <tar> <tar-sha256> <ref> <git-sha>
#                           verify archive hash, tag, and image revision; no swap
#   activate <ref>          swap the container to a locally loaded ref
#   rollback <ref>          restart the container on a retained local ref
#   deactivate              remove the candidate container
#   cutover-legacy          point nginx at the staged container, retaining legacy
#   rollback-legacy-ingress restore the saved nginx config and reload nginx
#
# INVARIANTS
#   * No `docker pull`; activation and rollback use only locally loaded images.
#   * State and secrets are host-mounted and must exist before container start.
#   * Previous images are never pruned here.
#   * First cutover keeps the legacy service on :8443 for immediate rollback.
set -euo pipefail

CONTAINER="${ARE_CONTAINER_NAME:-are-scanner-edge}"
STATE_DIR="${ARE_STATE_DIR:-/var/lib/are-scanner}"
SECRETS_ENV="${ARE_SECRETS_ENV:-/etc/are-scanner/secrets.env}"
ENGINE="${ARE_CONTAINER_ENGINE:-docker}"
BIND="${ARE_BIND:-127.0.0.1:8080}"
APP_PORT="${ARE_APP_PORT:-8077}"
CONTAINER_USER="${ARE_CONTAINER_USER:-}"
LEGACY_SERVICE="${ARE_LEGACY_SERVICE:-are-scanner-api.service}"
NGINX_SITE="${ARE_NGINX_SITE:-/etc/nginx/sites-available/scanner.backbond.ai}"
NGINX_BACKUP="${ARE_NGINX_BACKUP:-/etc/are-scanner/nginx-legacy-pre-container.conf}"
# shellcheck disable=SC1090
[ -f /etc/are-scanner/deploy.env ] && . /etc/are-scanner/deploy.env

log() { echo "[box_deploy] $*" >&2; }

current() {
  "${ENGINE}" inspect --format '{{ index .Config.Image }}' "${CONTAINER}" 2>/dev/null || true
}

# Docker archives do not reliably preserve registry RepoDigests across
# `docker save` / `docker load`. The signed manifest therefore binds the
# registry digest and the exact archive SHA-256. The box verifies the archive,
# then the loaded tag and immutable git revision label.
load() {
  local tarball="$1" want_tar_sha="$2" want_ref="$3" want_revision="$4"
  [ -n "${tarball}" ] || { log "load: missing tarball path"; exit 2; }
  [ -f "${tarball}" ] || { log "load: tarball not found: ${tarball}"; exit 2; }
  [ -n "${want_tar_sha}" ] || { log "load: missing expected archive sha256"; exit 2; }
  [ -n "${want_ref}" ] || { log "load: missing expected image ref"; exit 2; }
  [ -n "${want_revision}" ] || { log "load: missing expected git revision"; exit 2; }

  local got_tar_sha
  got_tar_sha="$(sha256sum "${tarball}" | awk '{print $1}')"
  [ "${got_tar_sha}" = "${want_tar_sha}" ] || {
    log "ARCHIVE HASH MISMATCH: expected ${want_tar_sha}, got ${got_tar_sha} — refusing to load"
    exit 5
  }

  log "archive hash verified (${want_tar_sha}); loading ${tarball}"
  "${ENGINE}" load -i "${tarball}" >&2
  "${ENGINE}" image inspect "${want_ref}" >/dev/null 2>&1 || {
    log "load: expected ref ${want_ref} is absent after load"
    exit 5
  }

  local got_revision
  got_revision="$("${ENGINE}" image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "${want_ref}" 2>/dev/null || true)"
  [ "${got_revision}" = "${want_revision}" ] || {
    log "REVISION MISMATCH: expected ${want_revision}, image label is ${got_revision:-<empty>} — refusing to activate"
    exit 5
  }
  log "loaded payload verified: ref=${want_ref} revision=${want_revision}"
  echo "${want_ref}"
}

run_container() {
  local ref="$1"
  [ -d "${STATE_DIR}" ] || { log "state directory missing: ${STATE_DIR}"; exit 8; }
  [ -f "${SECRETS_ENV}" ] || { log "secrets env file missing: ${SECRETS_ENV}"; exit 8; }

  "${ENGINE}" rm -f "${CONTAINER}" >/dev/null 2>&1 || true
  local host="${BIND%%:*}" port="${BIND##*:}"
  local args=(run -d --name "${CONTAINER}"
    --restart unless-stopped
    -p "${host}:${port}:${APP_PORT}"
    -v "${STATE_DIR}:/var/lib/are-scanner"
    --env-file "${SECRETS_ENV}")
  [ -n "${CONTAINER_USER}" ] && args+=(--user "${CONTAINER_USER}")
  "${ENGINE}" "${args[@]}" "${ref}"
}

activate() {
  local ref="$1"
  [ -n "${ref}" ] || { log "activate: missing ref"; exit 2; }
  "${ENGINE}" image inspect "${ref}" >/dev/null 2>&1 || {
    log "activate: image ${ref} not present locally — did you load it first?"
    exit 6
  }
  log "activate ${ref} (prev: $(current || echo none))"
  run_container "${ref}"
  log "activated ${ref}"
}

rollback() {
  local ref="$1"
  [ -n "${ref}" ] || { log "rollback: no previous ref supplied"; exit 3; }
  log "ROLLBACK -> ${ref}"
  "${ENGINE}" image inspect "${ref}" >/dev/null 2>&1 || {
    log "rollback: previous image ${ref} is absent locally; cannot self-heal"
    exit 7
  }
  run_container "${ref}"
  log "rolled back to ${ref}"
}

deactivate() {
  log "deactivate candidate container ${CONTAINER}"
  "${ENGINE}" rm -f "${CONTAINER}" >/dev/null 2>&1 || true
}

restore_legacy_ingress() {
  [ -f "${NGINX_BACKUP}" ] || {
    log "legacy nginx backup missing: ${NGINX_BACKUP}"
    return 9
  }
  cp -a "${NGINX_BACKUP}" "${NGINX_SITE}"
  nginx -t
  systemctl reload nginx
  log "nginx restored to legacy service on 127.0.0.1:8443"
}

cutover_legacy() {
  [ -f "${NGINX_SITE}" ] || { log "nginx site missing: ${NGINX_SITE}"; exit 9; }
  [ ! -e "${NGINX_BACKUP}" ] || {
    log "refusing to overwrite existing legacy nginx backup: ${NGINX_BACKUP}"
    exit 9
  }
  systemctl is-active --quiet "${LEGACY_SERVICE}" || {
    log "legacy service ${LEGACY_SERVICE} is not active; refusing first cutover"
    exit 9
  }
  [ "$("${ENGINE}" inspect --format '{{.State.Running}}' "${CONTAINER}" 2>/dev/null || true)" = "true" ] || {
    log "candidate container ${CONTAINER} is not running"
    exit 9
  }
  grep -q '127\.0\.0\.1:8443' "${NGINX_SITE}" || {
    log "nginx site does not point at the expected legacy port 8443"
    exit 9
  }

  cp -a "${NGINX_SITE}" "${NGINX_BACKUP}"
  local candidate
  candidate="$(mktemp "${NGINX_SITE}.candidate.XXXXXX")"
  sed "s#127\\.0\\.0\\.1:8443#${BIND}#g" "${NGINX_SITE}" > "${candidate}"
  grep -q "${BIND}" "${candidate}" || {
    log "failed to render nginx candidate for ${BIND}"
    rm -f "${candidate}"
    exit 9
  }
  install -o root -g root -m 0644 "${candidate}" "${NGINX_SITE}"
  rm -f "${candidate}"

  if ! nginx -t; then
    log "nginx validation failed; restoring legacy config"
    restore_legacy_ingress
    exit 9
  fi
  if ! systemctl reload nginx; then
    log "nginx reload failed; restoring legacy config"
    restore_legacy_ingress
    exit 9
  fi
  log "nginx now routes scanner traffic to ${BIND}; legacy service retained"
}

case "${1:-}" in
  current) current ;;
  load) load "${2:-}" "${3:-}" "${4:-}" "${5:-}" ;;
  activate) activate "${2:-}" ;;
  rollback) rollback "${2:-}" ;;
  deactivate) deactivate ;;
  cutover-legacy) cutover_legacy ;;
  rollback-legacy-ingress) restore_legacy_ingress ;;
  *)
    echo "usage: box_deploy {current|load <tarball> <tar-sha256> <ref> <git-sha>|activate <ref>|rollback <ref>|deactivate|cutover-legacy|rollback-legacy-ingress}" >&2
    exit 64
    ;;
esac
