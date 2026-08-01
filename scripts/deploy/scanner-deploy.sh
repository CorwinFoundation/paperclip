#!/usr/bin/env bash
# BEAAA-20076 - atomic, egress-sealed activation of the frozen Scanner image.
# Install only as /usr/local/sbin/scanner-deploy (root:root 0755).
set -Eeuo pipefail

SERVICE="${SCANNER_LIVE_SERVICE:-backbond-scanner-live.service}"
ENV_FILE="${SCANNER_EDGE_ENV:-/etc/backbond/scanner-edge.env}"
STATE_DIR="${SCANNER_STATE_DIR:-/var/lib/backbond-deploy-agent}"
UNIT_FILE="${SCANNER_UNIT_FILE:-/etc/systemd/system/backbond-scanner-live.service}"
LOCAL_URL="${SCANNER_READINESS_URL:-http://127.0.0.1:8077/openapi.json}"
PUBLIC_BASE="${SCANNER_PUBLIC_BASE_URL:-https://scanner.backbond.ai}"
DOCKER="${SCANNER_DOCKER:-docker}"
SYSTEMCTL="${SCANNER_SYSTEMCTL:-systemctl}"
CURL="${SCANNER_CURL:-curl}"
TIMEOUT="${SCANNER_READINESS_TIMEOUT:-60}"
INTERVAL="${SCANNER_READINESS_INTERVAL:-2}"
CONTAINER="${SCANNER_CONTAINER_NAME:-backbond-scanner-live-edge}"
EXPECTED_OWNER_UID="${SCANNER_EXPECTED_OWNER_UID:-0}"
EXPECTED_OWNER_GID="${SCANNER_EXPECTED_OWNER_GID:-0}"

FROZEN_SHA="${SCANNER_FROZEN_SHA:-7850289333ffc1de6eebea9c2201e0f1059c4c12}"
FROZEN_REPO_DIGEST="${SCANNER_FROZEN_REPO_DIGEST:-ghcr.io/corwinfoundation/are-scanner@sha256:e955a2812015cf7da487c2e266e0a7f3fc9478cdafc2580dc0e02df53d96df9b}"
FROZEN_ARCHIVE_SHA="${SCANNER_FROZEN_ARCHIVE_SHA:-502a52979825fe838fdccceac08d079fc553262bce69d69d06d904c2af94f1c0}"
FROZEN_MANIFEST_SHA="${SCANNER_FROZEN_MANIFEST_SHA:-2417a7b22c1ad82c7b3ad68eb815b03b6345be6b1c25bbcd54fc756a85957ddc}"
FROZEN_CANDIDATE="${SCANNER_FROZEN_CANDIDATE:-BEAAA-19984}"
REVISION_LABEL=org.opencontainers.image.revision
MANIFEST_LABEL=ai.backbond.release.manifest-sha256

log() { printf '[scanner-deploy] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }
require_root_owned() {
  [ -e "$1" ] || die "required path missing: $1"
  [ "$(stat -c %u "$1")" = "$EXPECTED_OWNER_UID" ] || die "required path is not root-owned: $1"
}
fsync_path() {
  python3 - "$1" <<'PY'
import os, sys
p=sys.argv[1]
fd=os.open(p, os.O_RDONLY | (os.O_DIRECTORY if os.path.isdir(p) else 0))
try: os.fsync(fd)
finally: os.close(fd)
PY
}
atomic_install() {
  src=$1 dst=$2 mode=$3
  tmp="$(dirname "$dst")/.scanner-deploy.$$.tmp"
  install -o "$EXPECTED_OWNER_UID" -g "$EXPECTED_OWNER_GID" -m "$mode" "$src" "$tmp"
  fsync_path "$tmp"
  mv -fT "$tmp" "$dst"
  fsync_path "$(dirname "$dst")"
}
snapshot() {
  src=$1 name=$2
  if [ -e "$src" ]; then cp -a "$src" "$TX/$name"; else : >"$TX/$name.ABSENT"; fi
}
restore_snapshot() {
  dst=$1 name=$2
  if [ -e "$TX/$name.ABSENT" ]; then rm -f -- "$dst"; else atomic_install "$TX/$name" "$dst" "$(stat -c %a "$TX/$name")"; fi
}
wait_200() {
  url=$1 out=${2:-/dev/null}; n=$((TIMEOUT / INTERVAL)); [ "$n" -gt 0 ] || n=1
  i=0; while [ "$i" -lt "$n" ]; do
    if [ "$("$CURL" -sS -o "$out" -w '%{http_code}' "$url" || true)" = 200 ]; then return 0; fi
    i=$((i+1)); [ "$i" -ge "$n" ] || sleep "$INTERVAL"
  done
  return 1
}

[ "$#" = 5 ] || die 'usage: scanner-deploy <commit-sha> <archive.tar> <repository@sha256:digest> <archive-sha256> <candidate-id>'
SHA=$1 ARCHIVE=$2 REPO_DIGEST=$3 ARCHIVE_SHA=$4 CANDIDATE=$5
case "$SHA" in (*[!0-9a-f]*|'') die 'commit must be lowercase hex';; esac
case "$ARCHIVE_SHA" in (*[!0-9a-f]*|'') die 'archive SHA-256 must be lowercase hex';; esac
case "$REPO_DIGEST" in (*[!a-zA-Z0-9._/@:-]*|'') die 'repository digest has invalid syntax';; esac
case "$CANDIDATE" in (BEAAA-[0-9]*) :;; (*) die 'candidate id has invalid syntax';; esac
[ "$SHA" = "$FROZEN_SHA" ] || die 'commit is not the frozen candidate'
[ "$REPO_DIGEST" = "$FROZEN_REPO_DIGEST" ] || die 'repository digest is not the frozen candidate'
[ "$ARCHIVE_SHA" = "$FROZEN_ARCHIVE_SHA" ] || die 'archive SHA argument is not the frozen candidate'
[ "$CANDIDATE" = "$FROZEN_CANDIDATE" ] || die 'candidate id is not the frozen candidate'
[ -f "$ARCHIVE" ] || die 'archive not found'
[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" = "$ARCHIVE_SHA" ] || die 'archive checksum mismatch'
require_root_owned "$ENV_FILE"; require_root_owned "$STATE_DIR"; require_root_owned "$UNIT_FILE"
[ "$(stat -c %a "$STATE_DIR")" = 755 ] || die 'state directory mode must be 0755'
[ "$(grep -c '^SCANNER_EDGE_IMAGE=' "$ENV_FILE")" -le 1 ] || die 'duplicate SCANNER_EDGE_IMAGE assignment'
[ "$(grep -c '^BACKBOND_RELEASE_IDENTITY_PATH=' "$ENV_FILE")" -le 1 ] || die 'duplicate BACKBOND_RELEASE_IDENTITY_PATH assignment'
grep -Fq -- '--volume /var/lib/backbond-deploy-agent:/var/lib/backbond-deploy-agent:ro' "$UNIT_FILE" || die 'unit lacks exact read-only identity mount'

# Loading is pre-mutation. No registry operation is permitted.
"$DOCKER" load -i "$ARCHIVE" >/dev/null
IMAGE_ID="$("$DOCKER" image ls --no-trunc --filter "label=$REVISION_LABEL=$SHA" --format '{{.ID}}' | awk 'NF' | sort -u)"
[ "$(printf '%s\n' "$IMAGE_ID" | awk 'NF{n++} END{print n+0}')" = 1 ] || die 'expected exactly one image with frozen OCI revision'
GOT_REV="$("$DOCKER" image inspect --format "{{ index .Config.Labels \"$REVISION_LABEL\" }}" "$IMAGE_ID")"
GOT_MANIFEST="$("$DOCKER" image inspect --format "{{ index .Config.Labels \"$MANIFEST_LABEL\" }}" "$IMAGE_ID")"
[ "$GOT_REV" = "$FROZEN_SHA" ] || die 'OCI revision mismatch'
[ "$GOT_MANIFEST" = "$FROZEN_MANIFEST_SHA" ] || die 'release-manifest SHA label mismatch'

LOCAL_TAG="backbond-scanner-live:$SHA"
RELEASES="$STATE_DIR/releases"; TRANSACTIONS="$STATE_DIR/transactions"; RECEIPTS="$STATE_DIR/receipts"
install -d -o "$EXPECTED_OWNER_UID" -g "$EXPECTED_OWNER_GID" -m 0755 "$RELEASES" "$TRANSACTIONS" "$RECEIPTS"
TX="$(mktemp -d "$TRANSACTIONS/$SHA.XXXXXXXX")"; chmod 0700 "$TX"
CURRENT="$STATE_DIR/current_version"; IDENTITY="$RELEASES/$SHA.json"
snapshot "$ENV_FILE" env; snapshot "$CURRENT" current_version; snapshot "$IDENTITY" candidate_identity
OLD_IDENTITY_PATH="$(sed -n 's/^BACKBOND_RELEASE_IDENTITY_PATH=//p' "$ENV_FILE")"
if [ -n "$OLD_IDENTITY_PATH" ] && [ "$OLD_IDENTITY_PATH" != "$IDENTITY" ]; then snapshot "$OLD_IDENTITY_PATH" prior_identity; else : >"$TX/prior_identity.NONE"; fi
transaction_active=1

rollback() {
  rc=${1:-1}; trap - ERR INT TERM; set +e
  [ "${transaction_active:-0}" = 1 ] || exit "$rc"
  log "ROLLBACK: restoring image, environment, markers, and identity"
  restore_snapshot "$ENV_FILE" env
  restore_snapshot "$CURRENT" current_version
  restore_snapshot "$IDENTITY" candidate_identity
  if [ ! -e "$TX/prior_identity.NONE" ]; then restore_snapshot "$OLD_IDENTITY_PATH" prior_identity; fi
  if ! "$SYSTEMCTL" restart "$SERVICE" || ! wait_200 "$LOCAL_URL"; then
    printf 'ROLLBACK_FAILED\n' >"$TX/ROLLBACK_FAILED"; fsync_path "$TX/ROLLBACK_FAILED"; fsync_path "$TX"
    log "ROLLBACK_FAILED: transaction retained at $TX"; exit 90
  fi
  if [ -n "$OLD_IDENTITY_PATH" ] && ! wait_200 "$PUBLIC_BASE/scanner/v1/release-identity" "$TX/prior-public.json"; then
    printf 'ROLLBACK_FAILED\n' >"$TX/ROLLBACK_FAILED"; fsync_path "$TX/ROLLBACK_FAILED"; fsync_path "$TX"
    log "ROLLBACK_FAILED: prior public identity did not recover; transaction retained at $TX"; exit 91
  fi
  log "rollback complete; transaction retained at $TX"; exit "$rc"
}
trap 'rollback $?' ERR INT TERM

"$DOCKER" tag "$IMAGE_ID" "$LOCAL_TAG"
RECORDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 - "$TX/identity.activating" "$SHA" "$REPO_DIGEST" "$LOCAL_TAG" "$CANDIDATE" "$RECORDED_AT" <<'PY'
import json,sys
p,sha,digest,image,candidate,at=sys.argv[1:]
v={"schema_version":"backbond-scanner-release-identity@1","environment":"production","host":"srv1749248","candidate_id":candidate,"commit_sha":sha,"image_digest":digest,"configured_image":image,"running_container_revision":sha,"internal_version_sha":sha,"current_version":sha,"status":"activating","recorded_at":at}
open(p,'w').write(json.dumps(v,sort_keys=True,separators=(',',':'))+'\n')
PY
python3 -m json.tool "$TX/identity.activating" >/dev/null
atomic_install "$TX/identity.activating" "$IDENTITY" 0444

awk '!/^SCANNER_EDGE_IMAGE=/ && !/^BACKBOND_RELEASE_IDENTITY_PATH=/' "$ENV_FILE" >"$TX/env.next"
printf 'SCANNER_EDGE_IMAGE=%s\nBACKBOND_RELEASE_IDENTITY_PATH=%s\n' "$LOCAL_TAG" "$IDENTITY" >>"$TX/env.next"
[ "$(grep -c '^SCANNER_EDGE_IMAGE=' "$TX/env.next")" = 1 ]
[ "$(grep -c '^BACKBOND_RELEASE_IDENTITY_PATH=' "$TX/env.next")" = 1 ]
printf '%s\n' "$SHA" >"$TX/current.next"
atomic_install "$TX/current.next" "$CURRENT" 0644
atomic_install "$TX/env.next" "$ENV_FILE" 0600
"$SYSTEMCTL" restart "$SERVICE"
"$SYSTEMCTL" is-active --quiet "$SERVICE"
wait_200 "$LOCAL_URL"
RUNNING_REV="$("$DOCKER" inspect --format "{{ index .Config.Labels \"$REVISION_LABEL\" }}" "$CONTAINER")"
[ "$RUNNING_REV" = "$SHA" ]
wait_200 "$PUBLIC_BASE/scanner/v1/sla"
wait_200 "$PUBLIC_BASE/scanner/v1/release-identity" "$TX/public-activating.json"

python3 - "$TX/public-activating.json" "$SHA" "$REPO_DIGEST" "$LOCAL_TAG" <<'PY'
import json,sys
p,sha,digest,image=sys.argv[1:]; d=json.load(open(p))
for k,v in {"commit_sha":sha,"current_version":sha,"running_container_revision":sha,"internal_version_sha":sha,"image_digest":digest,"configured_image":image}.items():
    if d.get(k)!=v: raise SystemExit(f"public identity mismatch: {k}")
PY
python3 - "$IDENTITY" <<'PY'
import json,os,sys,tempfile
p=sys.argv[1]; d=json.load(open(p)); d['status']='activated'
fd,t=tempfile.mkstemp(prefix='.identity.',dir=os.path.dirname(p)); os.fchmod(fd,0o444)
with os.fdopen(fd,'w') as f: json.dump(d,f,sort_keys=True,separators=(',',':')); f.write('\n'); f.flush(); os.fsync(f.fileno())
os.replace(t,p); fd=os.open(os.path.dirname(p),os.O_RDONLY|os.O_DIRECTORY); os.fsync(fd); os.close(fd)
PY
wait_200 "$PUBLIC_BASE/scanner/v1/release-identity" "$TX/public-activated.json"
python3 - "$IDENTITY" "$TX/public-activated.json" <<'PY'
import json,sys
expected=json.load(open(sys.argv[1])); observed=json.load(open(sys.argv[2]))
for key in ("schema_version","environment","host","candidate_id","commit_sha","image_digest","configured_image","running_container_revision","internal_version_sha","current_version","status"):
    if observed.get(key)!=expected.get(key): raise SystemExit(f"activated public identity mismatch: {key}")
PY

FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
OBSERVED_IMAGE_ID="$("$DOCKER" inspect --format '{{.Image}}' "$CONTAINER")"
python3 - "$TX/receipt.json" "$SHA" "$REPO_DIGEST" "$ARCHIVE_SHA" "$GOT_MANIFEST" "$CANDIDATE" "$IMAGE_ID" "$OBSERVED_IMAGE_ID" "$RECORDED_AT" "$FINISHED_AT" "$TX" "$TX/public-activated.json" <<'PY'
import hashlib,json,sys
p,sha,digest,archive,manifest,candidate,loaded,running,started,finished,tx,response=sys.argv[1:]
d={"schema_version":"backbond-scanner-activation-receipt@1","candidate_id":candidate,"commit_sha":sha,"repository_digest":digest,"archive_sha256":archive,"release_manifest_sha256":manifest,"loaded_image_id":loaded,"running_image_id":running,"started_at":started,"finished_at":finished,"transaction_backup_path":tx,"public_identity_response_sha256":hashlib.sha256(open(response,'rb').read()).hexdigest(),"status":"activated"}
open(p,'w').write(json.dumps(d,sort_keys=True,separators=(',',':'))+'\n')
PY
atomic_install "$TX/receipt.json" "$RECEIPTS/$SHA.json" 0444
transaction_active=0; trap - ERR INT TERM
log "OK: exact frozen candidate activated; receipt $RECEIPTS/$SHA.json"
