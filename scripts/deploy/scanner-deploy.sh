#!/usr/bin/env bash
# BEAAA-16598 — on-box, systemd-owned Scanner deploy helper.
#
# Interface:  scanner-deploy.sh <git-sha> [tar]
#
# Runs on the live edge host (srv1749248 topology, verified against
# 462aee92358993eb21f29d74d6f690765db3ae06). The host is EGRESS-SEALED: it has no
# registry egress. The image arrives as a tarball via SCP and is loaded locally.
# This helper owns the whole on-box swap under systemd:
#
#   1. optionally `docker load` the SCP-delivered tar,
#   2. select the image by EXACT OCI label org.opencontainers.image.revision=<sha>
#      (never a registry ref),
#   3. retag it to the local live tag  backbond-scanner-live:<git-sha>,
#   4. back up /etc/backbond/scanner-edge.env to  .pre-<sha>  BEFORE any mutation,
#   5. repoint SCANNER_EDGE_IMAGE at the local live tag (other lines preserved),
#   6. restart  backbond-scanner-live.service  (systemd owns the container),
#   7. wait boundedly for  http://127.0.0.1:8077/openapi.json,
#   8. on ANY post-backup failure: restore the backup and restart the retained
#      prior image, then exit non-zero.
#
# Install to /usr/local/sbin/scanner-deploy (root:root, 0755) and whitelist:
#
#   are-deploy ALL=(root) NOPASSWD: /usr/local/sbin/scanner-deploy
#
# INVARIANTS
#   * The image is only ever loaded from the local tar; never fetched from a
#     registry (the host has no registry egress).
#   * The live tag is a LOCAL tag, never a registry ref.
#   * systemd owns the container lifecycle; this helper only restarts the unit.
#   * The env file is backed up before it is touched; every failure after that
#     point self-heals by restoring the backup and restarting the prior image.
set -euo pipefail

SERVICE="${SCANNER_LIVE_SERVICE:-backbond-scanner-live.service}"
ENV_FILE="${SCANNER_EDGE_ENV:-/etc/backbond/scanner-edge.env}"
READINESS_URL="${SCANNER_READINESS_URL:-http://127.0.0.1:8077/openapi.json}"
DOCKER="${SCANNER_DOCKER:-docker}"
SYSTEMCTL="${SCANNER_SYSTEMCTL:-systemctl}"
CURL="${SCANNER_CURL:-curl}"
READINESS_TIMEOUT="${SCANNER_READINESS_TIMEOUT:-60}"
READINESS_INTERVAL="${SCANNER_READINESS_INTERVAL:-2}"

REVISION_LABEL="org.opencontainers.image.revision"
LOCAL_TAG_REPO="backbond-scanner-live"

log() { echo "[scanner-deploy] $*" >&2; }
die() { log "ERROR: $*"; exit 1; }

SHA="${1:-}"
TAR="${2:-}"
[ -n "${SHA}" ] || die "usage: scanner-deploy.sh <git-sha> [tar]"
case "${SHA}" in
  *[!0-9a-f]*) die "git-sha must be lowercase hex, got: ${SHA}" ;;
esac

LOCAL_TAG="${LOCAL_TAG_REPO}:${SHA}"
BACKUP="${ENV_FILE}.pre-${SHA}"

# 1. Load the SCP-delivered archive locally (egress-sealed host; no pull).
if [ -n "${TAR}" ]; then
  [ -f "${TAR}" ] || die "tar not found: ${TAR}"
  log "loading image archive ${TAR}"
  "${DOCKER}" load -i "${TAR}" >&2
fi

# 2. Select the image by EXACT revision label. Docker's label filter matches the
#    value exactly; we still re-inspect the winner to defend against any surprise.
IMAGE_ID="$(
  "${DOCKER}" image ls --no-trunc \
    --filter "label=${REVISION_LABEL}=${SHA}" \
    --format '{{.ID}}' | awk 'NF' | sort -u | head -n1
)"
[ -n "${IMAGE_ID}" ] || die "no locally loaded image with ${REVISION_LABEL}=${SHA}"
GOT_REV="$("${DOCKER}" image inspect \
  --format "{{ index .Config.Labels \"${REVISION_LABEL}\" }}" \
  "${IMAGE_ID}" 2>/dev/null || true)"
[ "${GOT_REV}" = "${SHA}" ] || die "revision label mismatch on ${IMAGE_ID}: got '${GOT_REV:-<empty>}', expected ${SHA}"
log "selected image ${IMAGE_ID} by ${REVISION_LABEL}=${SHA}"

# 3. Retag locally to the systemd-referenced live tag.
"${DOCKER}" tag "${IMAGE_ID}" "${LOCAL_TAG}"
log "retagged ${IMAGE_ID} -> ${LOCAL_TAG}"

# 4. Back up the env file BEFORE any mutation.
[ -f "${ENV_FILE}" ] || die "env file missing: ${ENV_FILE}"
cp -a "${ENV_FILE}" "${BACKUP}"
log "backed up ${ENV_FILE} -> ${BACKUP}"

# From here on, every failure restores the backup and restarts the prior image.
restore() {
  log "ROLLBACK: restoring ${BACKUP} and restarting ${SERVICE}"
  cp -a "${BACKUP}" "${ENV_FILE}"
  "${SYSTEMCTL}" restart "${SERVICE}" || log "rollback restart of ${SERVICE} FAILED — manual attention required"
}
rollback_and_die() { restore; die "$1"; }

# 5. Repoint SCANNER_EDGE_IMAGE at the local live tag; preserve every other line.
tmp="$(mktemp "${ENV_FILE}.next.XXXXXX")" || rollback_and_die "mktemp failed"
if grep -q '^SCANNER_EDGE_IMAGE=' "${ENV_FILE}"; then
  sed "s#^SCANNER_EDGE_IMAGE=.*#SCANNER_EDGE_IMAGE=${LOCAL_TAG}#" "${ENV_FILE}" > "${tmp}"
else
  cat "${ENV_FILE}" > "${tmp}"
  printf 'SCANNER_EDGE_IMAGE=%s\n' "${LOCAL_TAG}" >> "${tmp}"
fi
grep -qF "SCANNER_EDGE_IMAGE=${LOCAL_TAG}" "${tmp}" || { rm -f "${tmp}"; rollback_and_die "failed to repoint SCANNER_EDGE_IMAGE"; }
install -m 0600 "${tmp}" "${ENV_FILE}" || { rm -f "${tmp}"; rollback_and_die "failed to install repointed env file"; }
rm -f "${tmp}"
log "repointed SCANNER_EDGE_IMAGE=${LOCAL_TAG}"

# 6. Restart the systemd-owned live service.
"${SYSTEMCTL}" restart "${SERVICE}" || rollback_and_die "systemctl restart ${SERVICE} failed"
log "restarted ${SERVICE}"

# 7. Bounded readiness wait on the local app openapi surface.
iters=$(( READINESS_TIMEOUT / READINESS_INTERVAL ))
[ "${iters}" -ge 1 ] || iters=1
ready=0
i=0
while [ "${i}" -lt "${iters}" ]; do
  if "${CURL}" -fsS -o /dev/null "${READINESS_URL}"; then ready=1; break; fi
  i=$(( i + 1 ))
  [ "${i}" -lt "${iters}" ] && sleep "${READINESS_INTERVAL}"
done
[ "${ready}" -eq 1 ] || rollback_and_die "readiness timeout: ${READINESS_URL} not ready within ${READINESS_TIMEOUT}s"

log "OK: ${SERVICE} live on ${LOCAL_TAG}; ${READINESS_URL} ready"
