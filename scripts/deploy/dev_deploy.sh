#!/usr/bin/env bash
# BEAAA-16377 — build/sign/stage on merge; emit (but never run) the box push.
set -euo pipefail

TARGET="${BACKBOND_DEPLOY_TARGET:-are-deploy@srv1749248}"
BOX_DEPLOY="${BACKBOND_BOX_DEPLOY:-/usr/local/sbin/box_deploy}"
LIVE_VERSION_URL="${BACKBOND_LIVE_VERSION_URL:-https://scanner.backbond.ai/version}"
REMOTE_DIR="${BACKBOND_REMOTE_STAGING_DIR:-/var/tmp}"
IMAGE_REPOSITORY="${BACKBOND_IMAGE_REPOSITORY:-ghcr.io/corwinfoundation/are-scanner}"
STAGE_ROOT="${BACKBOND_DEPLOY_STAGE_ROOT:-artifacts/dev-deploy}"

ROOT="$(git rev-parse --show-toplevel)"
cd "${ROOT}"
SHA="$(git rev-parse HEAD)"
BRANCH="$(git branch --show-current)"
ORIGIN_MASTER="$(git rev-parse origin/master 2>/dev/null || true)"
REF="${IMAGE_REPOSITORY}:${SHA}"
STAGE_DIR="${STAGE_ROOT}/${SHA}"
ARCHIVE="${STAGE_DIR}/backbond-scanner-${SHA}.tar"
REMOTE_ARCHIVE="${REMOTE_DIR}/backbond-scanner-${SHA}.tar"

[ "${BRANCH}" = master ] || { echo "refusing deploy: branch is ${BRANCH}, expected master" >&2; exit 2; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "refusing deploy: tracked worktree is dirty" >&2; exit 2; }
git fetch origin master --quiet
ORIGIN_MASTER="$(git rev-parse origin/master)"
[ "${SHA}" = "${ORIGIN_MASTER}" ] || {
  echo "refusing deploy: local master ${SHA} is not full origin/master ${ORIGIN_MASTER}" >&2
  exit 2
}

command -v docker >/dev/null || { echo "docker is required" >&2; exit 3; }
command -v cosign >/dev/null || { echo "cosign is required" >&2; exit 3; }
mkdir -p "${STAGE_DIR}"

echo "[dev_deploy] building ${REF}"
docker build --build-arg "GIT_SHA=${SHA}" -t "${REF}" .
docker push "${REF}"
DIGEST="$(docker image inspect --format '{{index .RepoDigests 0}}' "${REF}" | sed 's/.*@//')"
case "${DIGEST}" in sha256:????????????????????????????????????????????????????????????????) ;; *) echo "invalid pushed digest: ${DIGEST}" >&2; exit 4 ;; esac
IMMUTABLE_REF="${IMAGE_REPOSITORY}@${DIGEST}"
cosign sign --yes "${IMMUTABLE_REF}"

docker save -o "${ARCHIVE}" "${REF}"
ARCHIVE_SHA="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
printf '%s\n' "${IMMUTABLE_REF}" > "${STAGE_DIR}/image-ref.txt"
printf '%s\n' "${ARCHIVE_SHA}" > "${STAGE_DIR}/archive.sha256"

# This is intentionally data, not execution. The agent/CI side has no SSH key,
# never opens an SSH connection, and stops after printing this single command.
printf -v Q_ARCHIVE '%q' "${ARCHIVE}"
printf -v Q_TARGET_ARCHIVE '%q' "${TARGET}:${REMOTE_ARCHIVE}"
printf -v Q_TARGET '%q' "${TARGET}"
printf -v Q_REMOTE_ARCHIVE '%q' "${REMOTE_ARCHIVE}"
printf -v Q_ARCHIVE_SHA '%q' "${ARCHIVE_SHA}"
printf -v Q_REF '%q' "${REF}"
printf -v Q_SHA '%q' "${SHA}"
printf -v Q_BOX_DEPLOY '%q' "${BOX_DEPLOY}"
printf -v Q_URL '%q' "${LIVE_VERSION_URL}"

COMMAND="scp ${Q_ARCHIVE} ${Q_TARGET_ARCHIVE} && ssh ${Q_TARGET} 'set -euo pipefail; previous=\$(sudo ${Q_BOX_DEPLOY} current); cleanup() { rm -f ${Q_REMOTE_ARCHIVE}; }; trap cleanup EXIT; sudo ${Q_BOX_DEPLOY} load ${Q_REMOTE_ARCHIVE} ${Q_ARCHIVE_SHA} ${Q_REF} ${Q_SHA} >/dev/null; sudo ${Q_BOX_DEPLOY} activate ${Q_REF}; response=\$(curl -fsS ${Q_URL}) || { [ -z \"\$previous\" ] || sudo ${Q_BOX_DEPLOY} rollback \"\$previous\"; exit 1; }; printf \"%s\" \"\$response\" | grep -F ${Q_SHA} >/dev/null || { [ -z \"\$previous\" ] || sudo ${Q_BOX_DEPLOY} rollback \"\$previous\"; exit 1; }; printf \"%s\\n\" \"\$response\"'"

echo "[dev_deploy] staged ${IMMUTABLE_REF} archive_sha256=${ARCHIVE_SHA}"
echo "[dev_deploy] operator command (agent side stops here; no box credential required):"
printf '%s\n' "${COMMAND}"
