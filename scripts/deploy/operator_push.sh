#!/usr/bin/env bash
# BEAAA-13362 — on-box operator push orchestrator (operator-mediated PUSH,
# ADR-0057 v2.0 / ADR BEAAA-13360, corrected by BEAAA-13387).
#
# The box has NO egress (BEAAA-13385) and no GitHub Actions runner, so the
# post-approval half of the pipeline runs HERE, invoked by the operator on the
# box after the Founder approved the release and the operator pushed the image
# tarball IN over the inbound operator SSH channel. This script keeps exactly
# the acceptance / auto-rollback / deploy-record contract cd.yml used to run,
# now on-box:
#
#   load+re-verify digest (box_deploy load) -> record previous image ->
#   activate -> acceptance_checks.sh -> AUTO-ROLLBACK on any failure ->
#   post_deploy_record.py (always).
#
# The Founder gate itself stays in cd.yml (`production` Environment). This
# script MUST only be run against a digest the release-manifest.json artifact
# authorized for THIS sha — the box_deploy `load` re-verifies that digest and
# fails closed on mismatch, so a wrong/tampered tarball never activates.
#
# Usage (operator, on the box):
#   operator_push.sh \
#     --tarball  /path/to/are-scanner-<sha>.tar \
#     --digest   sha256:<approved-digest-from-release-manifest> \
#     --ref      ghcr.io/corwinfoundation/are-scanner:<sha> \
#     --sha      <sha> \
#     --approver <founder-actor-from-manifest> \
#     --run-url  <actions-run-url-from-manifest> \
#     --manifest /path/to/release-manifest.json \
#     --manifest-sig /path/to/release-manifest.json.sig \
#     --manifest-pubkey /etc/are-scanner/cd-manifest-signing-pubkey.pem
#
# Box-local facts come from /etc/are-scanner/deploy.env + operator env:
#   PROD_BASE_URL, STAGING_TENANT_ID (never 'vapi'), STAGING_AGENT_ID,
#   SCAN_LOG_DB, ARE_STAGING_ADMIN_TOKEN, PAPERCLIP_API_URL,
#   PAPERCLIP_DEPLOY_BOT_KEY.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOX_DEPLOY="${ARE_BOX_DEPLOY:-/usr/local/sbin/box_deploy}"   # sudo-whitelisted
SUDO="${ARE_SUDO:-sudo}"

TARBALL=""; DIGEST=""; REF=""; SHA=""; APPROVER=""; RUN_URL=""
MANIFEST=""; MANIFEST_SIG=""; MANIFEST_PUBKEY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tarball)  TARBALL="$2"; shift 2;;
    --digest)   DIGEST="$2"; shift 2;;
    --ref)      REF="$2"; shift 2;;
    --sha)      SHA="$2"; shift 2;;
    --approver) APPROVER="$2"; shift 2;;
    --run-url)  RUN_URL="$2"; shift 2;;
    --manifest) MANIFEST="$2"; shift 2;;
    --manifest-sig) MANIFEST_SIG="$2"; shift 2;;
    --manifest-pubkey) MANIFEST_PUBKEY="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 64;;
  esac
done
for req in TARBALL DIGEST SHA MANIFEST MANIFEST_SIG MANIFEST_PUBKEY; do
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
      log "release manifest signature verification FAILED"; return 11;
    }

  MANIFEST="${MANIFEST}" SHA="${SHA}" DIGEST="${DIGEST}" REF="${REF}" python3 - <<'PY'
import json, os, sys
with open(os.environ["MANIFEST"]) as f:
    manifest = json.load(f)
expected = {
    "sha": os.environ["SHA"],
    "digest": os.environ["DIGEST"],
}
if os.environ.get("REF"):
    expected["ref"] = os.environ["REF"]
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

# Box-local facts (never echo secrets).
# shellcheck disable=SC1090
[ -f /etc/are-scanner/deploy.env ] && . /etc/are-scanner/deploy.env
BASE_URL="${PROD_BASE_URL:?PROD_BASE_URL required}"
STAGING_TENANT="${STAGING_TENANT_ID:?STAGING_TENANT_ID required}"
STAGING_AGENT="${STAGING_AGENT_ID:?STAGING_AGENT_ID required}"
SCAN_LOG_DB="${SCAN_LOG_DB:-/var/lib/are-scanner/scan_log.sqlite3}"
ADMIN_TOKEN="${ARE_STAGING_ADMIN_TOKEN:?ARE_STAGING_ADMIN_TOKEN required}"

ACCEPT_JSON="$(mktemp /tmp/acceptance.XXXXXX.json)"
OUTCOME="failure"
PREV=""

record_and_exit() {
  # Post the on-box deploy record no matter what (mirrors cd.yml if: always()).
  python3 "${HERE}/post_deploy_record.py" \
    --sha "${SHA}" \
    --ref "${REF}" \
    --digest "${DIGEST}" \
    --approver "${APPROVER}" \
    --outcome "${OUTCOME}" \
    --previous "${PREV}" \
    --acceptance "${ACCEPT_JSON}" \
    --run-url "${RUN_URL}" || true
  exit "${1:-1}"
}

# 1. Record the currently-active image (rollback target).
PREV="$(${SUDO} "${BOX_DEPLOY}" current || true)"
log "current image on box: ${PREV:-<none>}"

# 2. Re-verify the approved manifest signature on the box before touching the
#    container engine. The operator's off-box cosign image verification proves
#    GHCR provenance; this offline manifest signature is the box-side gate that
#    binds the passed SHA/ref/digest to the Founder-approved artifact.
verify_manifest || {
  log "approved manifest verification FAILED — refusing to load or activate"; OUTCOME="failure"; record_and_exit 10;
}

# 3. Load + on-box digest re-verify. Emits the verified ref on success.
VERIFIED_REF="$(${SUDO} "${BOX_DEPLOY}" load "${TARBALL}" "${DIGEST}")" || {
  log "load/digest re-verify FAILED — refusing to activate"; OUTCOME="failure"; record_and_exit 5;
}
# Prefer the human-readable REF the manifest named; fall back to the loaded ref.
ACTIVATE_REF="${REF:-${VERIFIED_REF}}"

# 4. Activate.
${SUDO} "${BOX_DEPLOY}" activate "${ACTIVATE_REF}" || {
  log "activate FAILED"; OUTCOME="failure"; record_and_exit 6;
}

# 5. Acceptance suite (health / auth×2 / staging self-scan never-vapi with the
#    3 non-null pins / BEAAA-13169 envelope 6-fields). Any failure -> rollback.
if "${HERE}/acceptance_checks.sh" \
      --base-url "${BASE_URL}" \
      --staging-tenant "${STAGING_TENANT}" \
      --staging-agent "${STAGING_AGENT}" \
      --scan-log-db "${SCAN_LOG_DB}" \
      --admin-token "${ADMIN_TOKEN}" \
      --out "${ACCEPT_JSON}"; then
  OUTCOME="success"
  log "acceptance PASSED — deploy ${SHA} live"
else
  OUTCOME="failure"
  log "acceptance FAILED"
  # 6. AUTO-ROLLBACK to the previously-active image (retained locally; no egress).
  if [ -n "${PREV}" ]; then
    log "AUTO-ROLLBACK -> ${PREV}"
    ${SUDO} "${BOX_DEPLOY}" rollback "${PREV}" || log "ROLLBACK ITSELF FAILED — paging operator"
    # Confirm the restored image is healthy.
    "${HERE}/acceptance_checks.sh" --base-url "${BASE_URL}" --health-only \
      --out "${ACCEPT_JSON}.rollback" || log "rollback health check FAILED — paging operator"
  else
    log "no previous image to roll back to — box may be down, paging operator"
  fi
fi

# 7. Deploy record (always).
record_and_exit "$([ "${OUTCOME}" = success ] && echo 0 || echo 1)"
