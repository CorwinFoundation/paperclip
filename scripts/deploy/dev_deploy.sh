#!/usr/bin/env bash
# BEAAA-16598 — build/sign/stage on merge; emit (but never run) the box push
# that hands the tar to the systemd-owned on-box helper scanner-deploy.sh.
#
# Trust boundary (Founder-selected path-b): the agent/CI side has no SSH key,
# never opens an SSH connection, and stops after printing a single command. The
# operator runs that command. The box is egress-sealed and loads the image from
# the SCP-delivered tar; the on-box helper owns image selection, the env
# repoint, the systemd restart, bounded readiness, and automatic rollback.
set -euo pipefail

TARGET="${BACKBOND_DEPLOY_TARGET:-are-deploy@srv1749248}"
SCANNER_DEPLOY_BIN="${BACKBOND_SCANNER_DEPLOY_BIN:-/usr/local/sbin/scanner-deploy}"
REMOTE_DIR="${BACKBOND_REMOTE_STAGING_DIR:-/var/tmp}"
IMAGE_REPOSITORY="${BACKBOND_IMAGE_REPOSITORY:-ghcr.io/corwinfoundation/are-scanner}"
STAGE_ROOT="${BACKBOND_DEPLOY_STAGE_ROOT:-artifacts/dev-deploy}"

ROOT="$(git rev-parse --show-toplevel)"
cd "${ROOT}"
SHA="$(git rev-parse HEAD)"
BRANCH="$(git branch --show-current)"
# The GHCR ref is provenance only (signed manifest). It is NEVER the live tag:
# the box selects by revision label and retags to backbond-scanner-live:<sha>.
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

# This is intentionally DATA, not execution. The agent/CI side has no SSH key,
# never opens an SSH connection, and stops after printing this single command.
# The emitted command SCPs the tar, then invokes the systemd-owned helper, which
# selects by revision label, retags, backs up the env, restarts the unit, waits
# on /openapi.json, and self-heals on failure.
printf -v Q_ARCHIVE '%q' "${ARCHIVE}"
printf -v Q_TARGET_ARCHIVE '%q' "${TARGET}:${REMOTE_ARCHIVE}"
printf -v Q_TARGET '%q' "${TARGET}"
printf -v Q_REMOTE_ARCHIVE '%q' "${REMOTE_ARCHIVE}"
printf -v Q_SHA '%q' "${SHA}"
printf -v Q_HELPER '%q' "${SCANNER_DEPLOY_BIN}"

COMMAND="scp ${Q_ARCHIVE} ${Q_TARGET_ARCHIVE} && ssh ${Q_TARGET} 'set -euo pipefail; cleanup() { rm -f ${Q_REMOTE_ARCHIVE}; }; trap cleanup EXIT; sudo ${Q_HELPER} ${Q_SHA} ${Q_REMOTE_ARCHIVE}'"

echo "[dev_deploy] staged ${IMMUTABLE_REF} archive_sha256=${ARCHIVE_SHA}"
echo "[dev_deploy] operator command (agent side stops here; no box credential required):"
printf '%s\n' "${COMMAND}"
