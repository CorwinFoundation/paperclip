#!/usr/bin/env bash
# PRE-PROMOTION LEGACY — ADR-0057 operator-mediated push lane (BEAAA-13362).
# This script remains live until Seq20 promotion completes (BEAAA-16214).
# For the systemd-owned deploy path introduced in BEAAA-16598, use scanner-deploy.sh.
#
# BEAAA-13362 — on-box operator push orchestrator (operator-mediated PUSH,
# ADR-0057 v2.0 / ADR BEAAA-13360, corrected by BEAAA-13387).
#
# The box has no registry egress. After Founder approval, the operator pushes
# the CI-produced image archive and signed release manifest onto the box.
#
# First production cutover is explicit:
#   operator_push.sh --first-cutover ...
# It starts the container on a parallel loopback port, runs acceptance there,
# switches nginx only after acceptance, verifies nginx, and leaves the legacy
# systemd service running on :8443 as the immediate rollback target.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOX_DEPLOY="${ARE_BOX_DEPLOY:-/usr/local/sbin/box_deploy}"
SUDO="${ARE_SUDO:-sudo}"

TARBALL=""
TAR_SHA256=""
DIGEST=""
REF=""
SHA=""
APPROVER=""
RUN_URL=""
MANIFEST=""
MANIFEST_SIG=""
FIRST_CUTOVER=0
while [ $# -gt 0 ]; do
  case "$1" in
    --tarball) TARBALL="$2"; shift 2 ;;
    --tar-sha256) TAR_SHA256="$2"; shift 2 ;;
    --digest) DIGEST="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --sha) SHA="$2"; shift 2 ;;
    --approver) APPROVER="$2"; shift 2 ;;
    --run-url) RUN_URL="$2"; shift 2 ;;
    --manifest) MANIFEST="$2"; shift 2 ;;
    --manifest-sig) MANIFEST_SIG="$2"; shift 2 ;;
    --first-cutover) FIRST_CUTOVER=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 64 ;;
  esac
done
for req in TARBALL TAR_SHA256 DIGEST REF SHA APPROVER RUN_URL MANIFEST MANIFEST_SIG; do
  [ -n "${!req}" ] || { echo "--${req,,} required" >&2; exit 64; }
done

log() { echo "[operator_push] $*" >&2; }

verify_manifest() {
  [ -f "${MANIFEST}" ] || { log "release manifest missing: ${MANIFEST}"; return 10; }
  [ -f "${MANIFEST_SIG}" ] || { log "release manifest signature missing: ${MANIFEST_SIG}"; return 10; }
  [ -f "${MANIFEST_PUBKEY}" ] || { log "manifest public key missing: ${MANIFEST_PUBKEY}"; return 10; }

  openssl dgst -sha256 \
    -verify "${MANIFEST_PUBKEY}" \
    -signature "${MANIFEST_SIG}" \
    "${MANIFEST}" >/dev/null || {
      log "release manifest signature verification FAILED"
      return 11
    }

  MANIFEST="${MANIFEST}" SHA="${SHA}" DIGEST="${DIGEST}" REF="${REF}" \
    TAR_SHA256="${TAR_SHA256}" APPROVER="${APPROVER}" RUN_URL="${RUN_URL}" \
    python3 - <<'PY'
import json
import os
import sys

with open(os.environ["MANIFEST"]) as f:
    manifest = json.load(f)
expected = {
    "sha": os.environ["SHA"],
    "digest": os.environ["DIGEST"],
    "ref": os.environ["REF"],
    "tar_sha256": os.environ["TAR_SHA256"],
    "approver": os.environ["APPROVER"],
    "run_url": os.environ["RUN_URL"],
}
errors = [
    f"{key}: manifest={manifest.get(key)!r} expected={value!r}"
    for key, value in expected.items()
    if manifest.get(key) != value
]
if manifest.get("delivery") != "operator-mediated-push":
    errors.append(f"delivery={manifest.get('delivery')!r} expected='operator-mediated-push'")
if manifest.get("manifest_signature") != "openssl-sha256-pem":
    errors.append("manifest_signature marker missing")
if errors:
    print("\n".join(errors), file=sys.stderr)
    sys.exit(12)
PY
}

# Box-local facts. Never echo secret values.
# The manifest trust anchor is selected only by the root-owned box config.
# shellcheck disable=SC1090
[ -f /etc/are-scanner/deploy.env ] && . /etc/are-scanner/deploy.env
MANIFEST_PUBKEY="${ARE_MANIFEST_PUBKEY:-/etc/are-scanner/cd-manifest-signing-pubkey.pem}"
STAGING_TENANT="${STAGING_TENANT_ID:?STAGING_TENANT_ID required}"
STAGING_AGENT="${STAGING_AGENT_ID:?STAGING_AGENT_ID required}"
STAGING_ENDPOINT="${STAGING_AGENT_ENDPOINT:?STAGING_AGENT_ENDPOINT required}"
ACCEPTANCE_SCANNER_VERSION="${ACCEPTANCE_SCANNER_VERSION:?ACCEPTANCE_SCANNER_VERSION required}"
ACCEPTANCE_CORPUS_VERSION="${ACCEPTANCE_CORPUS_VERSION:?ACCEPTANCE_CORPUS_VERSION required}"
ACCEPTANCE_CORPUS_SNAPSHOT_HASH="${ACCEPTANCE_CORPUS_SNAPSHOT_HASH:?ACCEPTANCE_CORPUS_SNAPSHOT_HASH required}"
ACCEPTANCE_SEED="${ACCEPTANCE_SEED:-42}"
SCAN_LOG_DB="${SCAN_LOG_DB:-/var/lib/are-scanner/scan_log.sqlite3}"
ADMIN_TOKEN="${ARE_STAGING_ADMIN_TOKEN:?ARE_STAGING_ADMIN_TOKEN required}"
LEGACY_SERVICE="${ARE_LEGACY_SERVICE:-are-scanner-api.service}"
if [ "${FIRST_CUTOVER}" -eq 1 ]; then
  BASE_URL="${CANDIDATE_BASE_URL:?CANDIDATE_BASE_URL required for first cutover}"
  PUBLIC_BASE_URL="${PUBLIC_BASE_URL:?PUBLIC_BASE_URL required for first cutover}"
else
  BASE_URL="${PROD_BASE_URL:?PROD_BASE_URL required}"
  PUBLIC_BASE_URL="${BASE_URL}"
fi

ACCEPT_JSON="$(mktemp /tmp/acceptance.XXXXXX.json)"
OUTCOME="failure"
PREV=""
PREVIOUS_RECORD=""
ACTIVATED=0
CUTOVER_DONE=0
FINISHED=0

post_record() {
  python3 "${HERE}/post_deploy_record.py" \
    --sha "${SHA}" \
    --ref "${REF}" \
    --digest "${DIGEST}" \
    --approver "${APPROVER}" \
    --outcome "${OUTCOME}" \
    --previous "${PREVIOUS_RECORD}" \
    --acceptance "${ACCEPT_JSON}" \
    --run-url "${RUN_URL}" || true
}

rollback_deployment() {
  local safe_to_deactivate=1
  if [ "${FIRST_CUTOVER}" -eq 1 ]; then
    if [ "${CUTOVER_DONE}" -eq 1 ]; then
      log "AUTO-ROLLBACK ingress -> legacy service"
      if ! ${SUDO} "${BOX_DEPLOY}" rollback-legacy-ingress; then
        log "legacy ingress rollback FAILED — keeping candidate container running and paging operator"
        safe_to_deactivate=0
      fi
    fi
    if [ "${safe_to_deactivate}" -eq 1 ]; then
      ${SUDO} "${BOX_DEPLOY}" deactivate || log "candidate cleanup FAILED"
    else
      log "candidate left running because Nginx may still route to it"
    fi
    "${HERE}/acceptance_checks.sh" --base-url "${PUBLIC_BASE_URL}" --health-only \
      --out "${ACCEPT_JSON}.rollback" || log "legacy rollback health check FAILED — paging operator"
  elif [ -n "${PREV}" ]; then
    log "AUTO-ROLLBACK -> ${PREV}"
    ${SUDO} "${BOX_DEPLOY}" rollback "${PREV}" || log "ROLLBACK ITSELF FAILED — paging operator"
    "${HERE}/acceptance_checks.sh" --base-url "${PUBLIC_BASE_URL}" --health-only \
      --out "${ACCEPT_JSON}.rollback" || log "rollback health check FAILED — paging operator"
  else
    log "no previous container image to roll back to — paging operator"
  fi
  ACTIVATED=0
}

record_and_exit() {
  local rc="${1:-1}"
  FINISHED=1
  post_record
  exit "${rc}"
}

on_exit() {
  local rc=$?
  if [ "${rc}" -ne 0 ] && [ "${FINISHED}" -eq 0 ]; then
    log "unexpected failure (rc=${rc})"
    [ "${ACTIVATED}" -eq 1 ] && rollback_deployment
    OUTCOME="failure"
    post_record
  fi
}
trap on_exit EXIT

PREV="$("${SUDO}" "${BOX_DEPLOY}" current || true)"
if [ "${FIRST_CUTOVER}" -eq 1 ]; then
  [ -z "${PREV}" ] || {
    log "--first-cutover requires no existing scanner container; found ${PREV}"
    record_and_exit 9
  }
  systemctl is-active --quiet "${LEGACY_SERVICE}" || {
    log "legacy rollback service ${LEGACY_SERVICE} is not active"
    record_and_exit 9
  }
  PREVIOUS_RECORD="legacy:${LEGACY_SERVICE}"
else
  PREVIOUS_RECORD="${PREV}"
fi
log "rollback target: ${PREVIOUS_RECORD:-<none>}"

verify_manifest || {
  log "approved manifest verification FAILED — refusing to load or activate"
  record_and_exit 10
}

VERIFIED_REF="$("${SUDO}" "${BOX_DEPLOY}" load "${TARBALL}" "${TAR_SHA256}" "${REF}" "${SHA}")" || {
  log "archive/ref/revision verification FAILED — refusing to activate"
  record_and_exit 5
}
[ "${VERIFIED_REF}" = "${REF}" ] || {
  log "verified ref mismatch: ${VERIFIED_REF} != ${REF}"
  record_and_exit 5
}

${SUDO} "${BOX_DEPLOY}" activate "${REF}" || {
  log "activate FAILED"
  record_and_exit 6
}
ACTIVATED=1

if ! "${HERE}/acceptance_checks.sh" \
  --base-url "${BASE_URL}" \
  --staging-tenant "${STAGING_TENANT}" \
  --staging-agent "${STAGING_AGENT}" \
  --staging-endpoint "${STAGING_ENDPOINT}" \
  --scanner-version "${ACCEPTANCE_SCANNER_VERSION}" \
  --corpus-version "${ACCEPTANCE_CORPUS_VERSION}" \
  --corpus-snapshot-hash "${ACCEPTANCE_CORPUS_SNAPSHOT_HASH}" \
  --seed "${ACCEPTANCE_SEED}" \
  --scan-log-db "${SCAN_LOG_DB}" \
  --admin-token "${ADMIN_TOKEN}" \
  --out "${ACCEPT_JSON}"; then
  log "candidate acceptance FAILED"
  rollback_deployment
  record_and_exit 1
fi

if [ "${FIRST_CUTOVER}" -eq 1 ]; then
  log "candidate acceptance passed; switching nginx while retaining ${LEGACY_SERVICE}"
  CUTOVER_DONE=1
  if ! ${SUDO} "${BOX_DEPLOY}" cutover-legacy; then
    log "nginx cutover FAILED"
    rollback_deployment
    record_and_exit 1
  fi
  if ! "${HERE}/acceptance_checks.sh" --base-url "${PUBLIC_BASE_URL}" --health-only \
    --out "${ACCEPT_JSON}.public"; then
    log "post-switch public health FAILED"
    rollback_deployment
    record_and_exit 1
  fi
  log "first cutover passed; legacy service remains active for immediate rollback"
fi

OUTCOME="success"
log "acceptance PASSED — deploy ${SHA} live"
record_and_exit 0
