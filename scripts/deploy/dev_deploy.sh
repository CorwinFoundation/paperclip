#!/usr/bin/env bash
# BEAAA-16355 — development-mode build + inbound push + activate + proof.
set -euo pipefail

TARGET="${BACKBOND_DEPLOY_TARGET:-are-deploy@srv1749248}"
IDENTITY="${BACKBOND_DEPLOY_IDENTITY:-}"
BOX_DEPLOY="${BACKBOND_BOX_DEPLOY:-/usr/local/sbin/box_deploy}"
LIVE_VERSION_URL="${BACKBOND_LIVE_VERSION_URL:-https://scanner.backbond.ai/version}"
REMOTE_DIR="${BACKBOND_REMOTE_STAGING_DIR:-/var/tmp}"

ROOT="$(git rev-parse --show-toplevel)"
cd "${ROOT}"
SHA="$(git rev-parse HEAD)"
BRANCH="$(git branch --show-current)"
REF="backbond-scanner-dev:${SHA}"
ARCHIVE="$(mktemp "/tmp/backbond-scanner-${SHA}.XXXXXX.tar")"
REMOTE_ARCHIVE="${REMOTE_DIR}/backbond-scanner-${SHA}.tar"
trap 'rm -f "${ARCHIVE}"' EXIT

[ "${BRANCH}" = master ] || { echo "refusing deploy: branch is ${BRANCH}, expected master" >&2; exit 2; }
[ -z "$(git status --porcelain)" ] || { echo "refusing deploy: worktree is dirty" >&2; exit 2; }
git fetch origin master --quiet
[ "${SHA}" = "$(git rev-parse origin/master)" ] || {
  echo "refusing deploy: local master ${SHA} is not origin/master" >&2
  exit 2
}

SSH=(ssh -o BatchMode=yes)
SCP=(scp -q -o BatchMode=yes)
if [ -n "${IDENTITY}" ]; then
  SSH+=(-i "${IDENTITY}")
  SCP+=(-i "${IDENTITY}")
fi

echo "[dev_deploy] building ${REF}"
docker build --build-arg "GIT_SHA=${SHA}" -t "${REF}" .
docker save -o "${ARCHIVE}" "${REF}"
ARCHIVE_SHA="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"

echo "[dev_deploy] pushing image archive into ${TARGET} (box egress is not used)"
"${SCP[@]}" "${ARCHIVE}" "${TARGET}:${REMOTE_ARCHIVE}"

PREVIOUS="$("${SSH[@]}" "${TARGET}" "sudo '${BOX_DEPLOY}' current")"
cleanup_remote() {
  "${SSH[@]}" "${TARGET}" "rm -f '${REMOTE_ARCHIVE}'" >/dev/null 2>&1 || true
}
trap 'cleanup_remote; rm -f "${ARCHIVE}"' EXIT

"${SSH[@]}" "${TARGET}" \
  "sudo '${BOX_DEPLOY}' load '${REMOTE_ARCHIVE}' '${ARCHIVE_SHA}' '${REF}' '${SHA}' >/dev/null && sudo '${BOX_DEPLOY}' activate '${REF}'"

if ! RESPONSE="$("${SSH[@]}" "${TARGET}" "curl -fsS '${LIVE_VERSION_URL}'")"; then
  [ -z "${PREVIOUS}" ] || "${SSH[@]}" "${TARGET}" "sudo '${BOX_DEPLOY}' rollback '${PREVIOUS}'"
  echo "live version check failed; rolled back to ${PREVIOUS:-<none>}" >&2
  exit 1
fi

case "${RESPONSE}" in
  *"${SHA}"*) ;;
  *)
    [ -z "${PREVIOUS}" ] || "${SSH[@]}" "${TARGET}" "sudo '${BOX_DEPLOY}' rollback '${PREVIOUS}'"
    echo "live version mismatch: expected ${SHA}, got ${RESPONSE}; rolled back" >&2
    exit 1
    ;;
esac

printf '%s\n' "${RESPONSE}"
echo "[dev_deploy] ${SHA} is live on ${TARGET}"
