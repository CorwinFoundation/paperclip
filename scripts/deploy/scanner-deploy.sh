#!/usr/bin/env bash
# BEAAA-22831 - atomic, egress-sealed activation of a Development Mode image.
# BEAAA-42199 - automatic deploy-helper refresh contract v1.
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
ARCHIVE_DIR="${SCANNER_ARCHIVE_DIR:-/var/tmp}"
ACTIVATION_LOCK="${SCANNER_ACTIVATION_LOCK_PATH:-$STATE_DIR/activation.lock}"
# BEAAA-31542: retained-evidence export. The live unit bind-mounts the state
# directory read-write at the same path on the host, so the evidence is already
# on disk; only the 0710 mode on the state directory keeps the deploy seat out.
EVIDENCE_STATE_DIR="${SCANNER_EVIDENCE_STATE_DIR:-/var/lib/are-scanner}"
EVIDENCE_EXPORT_DIR="${SCANNER_EVIDENCE_EXPORT_DIR:-/var/tmp/are-scanner-evidence-exports}"
SCAN_INTAKE_SERVICE="${SCANNER_SCAN_INTAKE_SERVICE:-backbond-scan-intake.service}"
DISK_PATH="${SCANNER_DISK_PATH:-/}"
DISK_MIN_FREE_BYTES="${SCANNER_DISK_MIN_FREE_BYTES:-21474836480}"
DISK_MIN_FREE_INODE_PERCENT="${SCANNER_DISK_MIN_FREE_INODE_PERCENT:-10}"
DISK_RETENTION_DAYS="${SCANNER_DISK_RETENTION_DAYS:-7}"
PROTECTED_RECOVERY_ARCHIVE="${SCANNER_PROTECTED_RECOVERY_ARCHIVE:-/root/backbond-stage-b-dbcee248-recovered-20260929.tar}"

REVISION_LABEL=org.opencontainers.image.revision
# BEAAA-23948: the Development Mode helper remains argument-driven, but this
# commit has two published GHCR manifests. Fail closed on the one canonical
# repository digest instead of accepting whichever tag an operator resolved.
CANONICAL_C4_SHA=c4a58caac801d24e7efa2436dbc5c4086b05d5fe
CANONICAL_C4_REPO_DIGEST=ghcr.io/corwinfoundation/are-scanner@sha256:b9bd0e70943424331c67c07ad17682412871d4cddde69d97193f847cd0fccfdd

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

public_dns_preflight() {
  # Run through the host resolver, with bounded time and no proxy/DNS bypass.
  # Failure here must not drain scans, load images, or enter a rollback transaction.
  local rc=0
  "$CURL" --noproxy '*' --fail --silent --show-error \
    --connect-timeout 5 --max-time 15 --output /dev/null \
    "$PUBLIC_BASE/scanner/v1/sla" || rc=$?
  case "$rc" in
    0) log 'public DNS/readiness preflight passed';;
    6) die 'public DNS preflight failed before activation; check systemd-resolved and current IANA root trust anchors';;
    *) die "public readiness preflight failed before activation (curl exit $rc)";;
  esac
}

container_env_value() {
  key=$1
  "$DOCKER" exec "$CONTAINER" printenv "$key" 2>/dev/null || true
}

diagnostics_wp_s() {
  customer_agent="$(container_env_value ARE_WALKER_CUSTOMER_AGENT)"
  scan_tier="$(container_env_value ARE_SCAN_TIER)"
  pinned_corpus_hash="$(container_env_value ARE_PINNED_CORPUS_SNAPSHOT_HASH)"
  scan_webhook="$(container_env_value ARE_SCAN_WEBHOOK)"
  resi_proxy_url="$(container_env_value BEAAA_RESI_PROXY_URL)"
  outbound_ip="$(
    "$DOCKER" exec "$CONTAINER" sh -c \
      'unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy; exec curl -fsS --max-time 10 https://api.ipify.org'
  )" || die 'cannot determine direct outbound IP'

  printf 'ARE_WALKER_CUSTOMER_AGENT=%s\n' "$customer_agent"
  printf 'ARE_SCAN_TIER=%s\n' "$scan_tier"
  if [ -n "$pinned_corpus_hash" ]; then
    printf 'ARE_PINNED_CORPUS_SNAPSHOT_HASH=set\n'
  else
    printf 'ARE_PINNED_CORPUS_SNAPSHOT_HASH=unset\n'
  fi
  printf 'ARE_SCAN_WEBHOOK=%s\n' "$scan_webhook"
  if [ -n "$resi_proxy_url" ]; then
    printf 'BEAAA_RESI_PROXY_URL=set\n'
  else
    printf 'BEAAA_RESI_PROXY_URL=unset\n'
  fi
  printf 'OUTBOUND_IP=%s\n' "$outbound_ip"
}

if [ "$#" = 2 ] && [ "$1" = diagnostics ] && [ "$2" = wp-s ]; then
  diagnostics_wp_s
  exit 0
fi

remove_stale_image_ref() {
  image_ref=$1
  if removal_output="$("$DOCKER" image rm "$image_ref" 2>&1)"; then
    return 0
  fi
  if printf '%s\n' "$removal_output" | grep -Eqi 'conflict:.*(running container|container .* is using)'; then
    log "retained stale image reference used by a running container: $image_ref"
    return 0
  fi
  printf '%s\n' "$removal_output" >&2
  return 1
}

reclaim_deploy_space() {
  current_ref="$(sed -n 's/^SCANNER_EDGE_IMAGE=//p' "$ENV_FILE")"
  [ -n "$current_ref" ] || die 'current scanner image is not configured'
  current_id="$("$DOCKER" image inspect --format '{{.ID}}' "$current_ref")" \
    || die 'cannot resolve current scanner image'
  rollback_ref="$(python3 - "$STATE_DIR/releases" "$current_ref" <<'PY'
import glob, json, os, sys
release_dir, current = sys.argv[1:]
candidates = []
for path in glob.glob(os.path.join(release_dir, '*.json')):
    try:
        data = json.load(open(path, encoding='utf-8'))
    except (OSError, ValueError):
        continue
    ref = data.get('configured_image')
    if ref and ref != current and data.get('status') == 'activated':
        candidates.append((data.get('recorded_at', ''), path, ref))
print(max(candidates)[2] if candidates else '')
PY
)"
  rollback_id=
  if [ -n "$rollback_ref" ]; then
    rollback_id="$("$DOCKER" image inspect --format '{{.ID}}' "$rollback_ref")" \
      || die 'cannot resolve rollback scanner image'
  fi

  # Active containers are never pruned. Remove stale stopped containers first so
  # their image references cannot pin obsolete scanner layers.
  "$DOCKER" container prune -f >/dev/null
  # Remove repository references, not image IDs. Docker rejects an unforced ID
  # removal when one image is referenced by multiple repositories; removing its
  # refs individually releases the same layers without broad --force deletion.
  while IFS=$'\t' read -r image_ref image_id; do
    [ -n "$image_ref" ] || continue
    case "$image_ref" in
      ghcr.io/corwinfoundation/are-scanner:*|backbond-scanner-live:*|'<none>:<none>') ;;
      *) continue ;;
    esac
    [ "$image_id" = "$current_id" ] && continue
    [ -n "$rollback_id" ] && [ "$image_id" = "$rollback_id" ] && continue
    if [ "$image_ref" = '<none>:<none>' ]; then
      remove_stale_image_ref "$image_id"
    else
      remove_stale_image_ref "$image_ref"
    fi
  done < <("$DOCKER" image ls --no-trunc --filter "label=$REVISION_LABEL" \
    --format '{{.Repository}}:{{.Tag}}\t{{.ID}}' | sort -u)
  "$DOCKER" image prune -f >/dev/null
  "$DOCKER" builder prune -f >/dev/null
  find "$ARCHIVE_DIR" -maxdepth 1 -type f -name 'backbond-scanner-*.tar' ! -path "$ARCHIVE" -delete
  log 'reclaimed stale deploy data; retained current and keep-last rollback images'
}

# BEAAA-36769: bounded, dry-run-first disk diagnostics and retention. Only
# helper-owned stale temporaries and dangling Docker images are candidates.
# Tagged active/rollback images, deploy state (including receipts), credentials,
# application data, and the named recovery archive cannot enter either set.
disk_maintenance() {
  mode=dry-run
  case "${1:-}" in
    '') ;;
    --apply) mode=apply ;;
    *) die 'usage: scanner-deploy disk-maintenance [--apply]' ;;
  esac
  [[ "$DISK_MIN_FREE_BYTES" =~ ^[0-9]+$ ]] || die 'SCANNER_DISK_MIN_FREE_BYTES must be an integer'
  [[ "$DISK_MIN_FREE_INODE_PERCENT" =~ ^[0-9]+$ ]] || die 'SCANNER_DISK_MIN_FREE_INODE_PERCENT must be an integer'
  [[ "$DISK_RETENTION_DAYS" =~ ^[0-9]+$ ]] || die 'SCANNER_DISK_RETENTION_DAYS must be an integer'
  [ "$DISK_MIN_FREE_INODE_PERCENT" -le 100 ] || die 'inode threshold must be at most 100 percent'

  read -r free_bytes free_inodes total_inodes <<EOF
$(df -Pk --block-size=1 "$DISK_PATH" | awk 'NR==2 {print $4}') $(df -Pi "$DISK_PATH" | awk 'NR==2 {print $4, $2}')
EOF
  [ -n "$free_bytes" ] && [ -n "$free_inodes" ] && [ -n "$total_inodes" ] || die 'cannot read disk diagnostics'
  inode_percent=$((free_inodes * 100 / total_inodes))
  log "disk path=$DISK_PATH free_bytes=$free_bytes free_inodes=$free_inodes free_inode_percent=$inode_percent"
  "$DOCKER" system df >&2 || die 'cannot read Docker disk usage'

  temp_list="$(mktemp)" || die 'cannot allocate disk-maintenance temporary list'
  image_list="$(mktemp)" || { rm -f -- "$temp_list"; die 'cannot allocate disk-maintenance image list'; }
  trap 'rm -f -- "$temp_list" "$image_list"' RETURN
  find "$ARCHIVE_DIR" -xdev -maxdepth 1 -type f -name '.scanner-deploy.*.tmp' \
    -mtime "+$DISK_RETENTION_DAYS" -print0 >"$temp_list"
  "$DOCKER" image ls --filter dangling=true --format '{{.ID}}' | awk 'NF' | sort -u | while IFS= read -r image_id; do
    created="$($DOCKER image inspect --format '{{.Created}}' "$image_id")" || exit 1
    python3 - "$image_id" "$created" "$DISK_RETENTION_DAYS" <<'PY'
from datetime import datetime, timezone
import sys
image_id, created, days = sys.argv[1:]
stamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
if (datetime.now(timezone.utc) - stamp).total_seconds() > int(days) * 86400:
    print(image_id)
PY
  done >"$image_list" || die 'cannot identify dangling Docker images'

  while IFS= read -r -d '' path; do
    [ "$path" != "$PROTECTED_RECOVERY_ARCHIVE" ] || die 'protected recovery archive selected'
    log "$mode temporary: $path"
    [ "$mode" = dry-run ] || rm -f -- "$path"
  done <"$temp_list"
  while IFS= read -r image_id; do
    [ -n "$image_id" ] || continue
    log "$mode dangling-image: $image_id"
    [ "$mode" = dry-run ] || "$DOCKER" image rm "$image_id" >/dev/null
  done <"$image_list"
  rm -f -- "$temp_list" "$image_list"
  trap - RETURN

  # Re-read after apply; dry-run intentionally reports current headroom.
  free_bytes="$(df -Pk --block-size=1 "$DISK_PATH" | awk 'NR==2 {print $4}')"
  read -r free_inodes total_inodes <<EOF
$(df -Pi "$DISK_PATH" | awk 'NR==2 {print $4, $2}')
EOF
  inode_percent=$((free_inodes * 100 / total_inodes))
  [ "$free_bytes" -ge "$DISK_MIN_FREE_BYTES" ] || die "free bytes below minimum: $free_bytes < $DISK_MIN_FREE_BYTES"
  [ "$inode_percent" -ge "$DISK_MIN_FREE_INODE_PERCENT" ] || die "free inodes below minimum: $inode_percent% < $DISK_MIN_FREE_INODE_PERCENT%"
  log "OK: disk headroom meets minimums (mode=$mode)"
}

# BEAAA-31542: `scanner-deploy export-evidence` copies the retained-evidence
# subtree out to a directory the invoking seat user owns, with a per-file
# sha256 manifest. Self-contained on purpose: the 2026-09-10 07:00Z run failed
# on a companion file in /tmp, so this depends on nothing outside the helper.
export_evidence() {
  # BEAAA-31116: the rank-2 roster's evidence refs are evidence://<job-id>/<test>/<case>.json,
  # which resolve under $EVIDENCE_STATE_DIR/evidence/<job-id> (tools/registered_case_executor.py
  # and docs/runbooks/full-scan-execution-layer.md), a different subtree from retained-evidence.
  case "${1:-}" in
    '')
      subtree='retained-evidence'
      base_kind='retained-evidence'
      missing_message='retained-evidence directory missing'
      ;;
    job)
      [ "$#" = 2 ] || die 'usage: scanner-deploy export-evidence job <job-id>'
      job_id="$2"
      [[ "$job_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] \
        || die 'job id must be a lowercase uuid'
      [ ! -L "$EVIDENCE_STATE_DIR/evidence" ] || die 'evidence directory is a symlink'
      subtree="evidence/$job_id"
      base_kind="job-evidence-$job_id"
      missing_message='job evidence directory missing'
      ;;
    *)
      die 'usage: scanner-deploy export-evidence [job <job-id>]'
      ;;
  esac
  [ ! -L "$EVIDENCE_STATE_DIR" ] || die 'evidence state directory is a symlink'
  src="$EVIDENCE_STATE_DIR/$subtree"
  [ ! -L "$src" ] || die "$subtree is a symlink"
  [ -d "$src" ] || die "$missing_message: $src"
  out_uid="${SUDO_UID:-$(id -u)}"
  out_gid="${SUDO_GID:-$(id -g)}"
  [[ "$out_uid" =~ ^[0-9]+$ ]] || die 'SUDO_UID is not numeric'
  [[ "$out_gid" =~ ^[0-9]+$ ]] || die 'SUDO_GID is not numeric'
  install -d -m 0755 "$EVIDENCE_EXPORT_DIR"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  base="$base_kind-$stamp"
  work="$(mktemp -d)" || die 'cannot create export staging directory'
  trap 'rm -rf -- "$work"' EXIT INT TERM
  # No --dereference: a symlink inside the evidence tree is archived as a
  # symlink, never followed out of the tree by a root-owned tar.
  tar --numeric-owner --owner=0 --group=0 -czf "$work/$base.tar.gz" \
    -C "$EVIDENCE_STATE_DIR" "$subtree" || die 'cannot archive retained evidence'
  python3 - "$src" "$work/$base.tar.gz" "$work/$base.manifest.json" "$stamp" <<'PY'
import hashlib, json, os, sys
src, archive, out, stamp = sys.argv[1:5]

def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()

entries = []
for root, dirs, names in os.walk(src):
    dirs.sort()
    for name in sorted(names):
        path = os.path.join(root, name)
        rel = os.path.relpath(path, src)
        if os.path.islink(path):
            entries.append({"path": rel, "type": "symlink"})
        elif os.path.isfile(path):
            entries.append({"path": rel, "type": "file",
                            "size_bytes": os.path.getsize(path),
                            "sha256": digest(path)})
        else:
            entries.append({"path": rel, "type": "other"})
manifest = {
    "schema_version": "backbond-retained-evidence-export@1",
    "exported_at": stamp,
    "source_dir": src,
    "archive_name": os.path.basename(archive),
    "archive_sha256": digest(archive),
    "file_count": sum(1 for e in entries if e["type"] == "file"),
    "entries": entries,
}
with open(out, 'w', encoding='utf-8') as fh:
    json.dump(manifest, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
  [ -s "$work/$base.manifest.json" ] || die 'cannot write export manifest'
  if [ "$(id -u)" = 0 ]; then
    install -o "$out_uid" -g "$out_gid" -m 0640 "$work/$base.tar.gz" "$EVIDENCE_EXPORT_DIR/$base.tar.gz"
    install -o "$out_uid" -g "$out_gid" -m 0640 "$work/$base.manifest.json" "$EVIDENCE_EXPORT_DIR/$base.manifest.json"
  else
    install -m 0640 "$work/$base.tar.gz" "$EVIDENCE_EXPORT_DIR/$base.tar.gz"
    install -m 0640 "$work/$base.manifest.json" "$EVIDENCE_EXPORT_DIR/$base.manifest.json"
  fi
  log "OK: exported $subtree for uid $out_uid"
  printf '%s\n%s\n' "$EVIDENCE_EXPORT_DIR/$base.tar.gz" "$EVIDENCE_EXPORT_DIR/$base.manifest.json"
}

if [ "${1:-}" = export-evidence ]; then
  shift
  export_evidence "$@"
  exit 0
fi

if [ "${1:-}" = disk-maintenance ]; then
  shift
  disk_maintenance "$@"
  exit 0
fi

# BEAAA-40283: deploy the host-managed scan-intake service from the exact image
# that the master lane has already authenticated and activated. This keeps the
# deploy seat on its single sudo-authorized helper and removes per-change root
# installation steps for scan-intake.
deploy_scan_intake() {
  [ "$#" = 1 ] || die 'usage: scanner-deploy scan-intake <commit-sha>'
  expected=$1
  [[ "$expected" =~ ^[0-9a-f]{40}$ ]] || die 'scan-intake commit must be 40 lowercase hex characters'
  running="$($DOCKER inspect --format "{{ index .Config.Labels \"$REVISION_LABEL\" }}" "$CONTAINER")" \
    || die 'cannot inspect live scanner container'
  [ "$running" = "$expected" ] || die "live revision mismatch: expected $expected, running $running"

  work="$(mktemp -d)" || die 'cannot create scan-intake staging directory'
  trap 'rm -rf -- "$work"' RETURN
  "$DOCKER" cp "$CONTAINER:/app/backbond/scan-intake/." "$work/" \
    || die 'cannot extract scan-intake payload from live image'
  [ -f "$work/update-server.sh" ] || die 'scan-intake payload lacks update-server.sh'
  SCAN_INTAKE_SERVICE="$SCAN_INTAKE_SERVICE" bash "$work/update-server.sh"
  rm -rf -- "$work"
  trap - RETURN
  log "OK: scan-intake deployed from $running"
}

if [ "${1:-}" = scan-intake ]; then
  shift
  deploy_scan_intake "$@"
  exit 0
fi

# BEAAA-39988: narrowly scoped stdin bridge for the idempotent starter-v5
# acceptance program. This lets the existing sudo-authorized deploy helper run
# the check without granting the deploy account general Docker access.
if [ "${1:-}" = acceptance ] && [ "${2:-}" = starter-v5 ]; then
  [ "$#" = 3 ] || die 'usage: scanner-deploy acceptance starter-v5 <commit-prefix>'
  expected=$3
  [[ "$expected" =~ ^[0-9a-f]{7,40}$ ]] || die 'acceptance commit must be a 7-40 character lowercase hex prefix'
  running="$($DOCKER inspect --format "{{ index .Config.Labels \"$REVISION_LABEL\" }}" "$CONTAINER")" \
    || die 'cannot inspect live scanner container'
  case "$running" in "$expected"*) ;; *) die "live revision mismatch: expected $expected, running $running" ;; esac
  ACCEPTANCE_EXPECTED_REVISION="$running" "$DOCKER" exec -i \
    -e "ACCEPTANCE_EXPECTED_REVISION=$running" "$CONTAINER" python3 -
  exit $?
fi

if [ "${1:-}" = preflight ]; then
  [ "$#" = 1 ] || die 'usage: scanner-deploy preflight'
  public_dns_preflight
  exit 0
fi

if [ "${1:-}" = refresh-helper ]; then
  [ "$#" = 4 ] || die 'usage: scanner-deploy refresh-helper <commit-sha> <staged-helper> <helper-sha256>'
  REFRESH_SHA=$2 REFRESH_SOURCE=$3 REFRESH_DIGEST=$4
  [[ "$REFRESH_SHA" =~ ^[0-9a-f]{40}$ ]] || die 'refresh commit must be 40 lowercase hex characters'
  [ "$REFRESH_SOURCE" = "$ARCHIVE_DIR/scanner-deploy-$REFRESH_SHA" ] || die 'refresh source is not the fixed helper path'
  [[ "$REFRESH_DIGEST" =~ ^[0-9a-f]{64}$ ]] || die 'helper SHA-256 must be 64 lowercase hex characters'
  [ -f "$REFRESH_SOURCE" ] && [ ! -L "$REFRESH_SOURCE" ] || die 'staged helper must be a regular file'
  [ "$(sha256sum "$REFRESH_SOURCE" | awk '{print $1}')" = "$REFRESH_DIGEST" ] || die 'staged helper checksum mismatch'
  grep -Fqx '# BEAAA-42199 - automatic deploy-helper refresh contract v1.' "$REFRESH_SOURCE" || die 'staged helper lacks refresh contract marker'
  bash -n "$REFRESH_SOURCE" || die 'staged helper has invalid shell syntax'
  atomic_install "$REFRESH_SOURCE" "$0" 0755
  log "OK: deploy helper refreshed for $REFRESH_SHA ($REFRESH_DIGEST)"
  exit 0
fi

[ "$#" = 5 ] || die 'usage: scanner-deploy <commit-sha> <archive.tar> <repository@sha256:digest> <archive-sha256> <candidate-id> | scanner-deploy refresh-helper <commit-sha> <staged-helper> <helper-sha256> | scanner-deploy preflight | scanner-deploy scan-intake <commit-sha> | scanner-deploy export-evidence [job <job-id>] | scanner-deploy disk-maintenance [--apply] | scanner-deploy acceptance starter-v5 <commit-prefix>'
SHA=$1 ARCHIVE=$2 REPO_DIGEST=$3 ARCHIVE_SHA=$4 CANDIDATE=$5
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || die 'commit must be 40 lowercase hex characters'
[[ "$ARCHIVE_SHA" =~ ^[0-9a-f]{64}$ ]] || die 'archive SHA-256 must be 64 lowercase hex characters'
[[ "$REPO_DIGEST" =~ ^[a-zA-Z0-9._/-]+@sha256:[0-9a-f]{64}$ ]] || die 'repository digest must be an immutable repository@sha256 reference'
[[ "$CANDIDATE" =~ ^BEAAA-[0-9]+$ ]] || die 'candidate id has invalid syntax'
if [ "$SHA" = "$CANONICAL_C4_SHA" ] && [ "$REPO_DIGEST" != "$CANONICAL_C4_REPO_DIGEST" ]; then
  die 'repository digest is not the canonical c4a58caa image'
fi
[ "$ARCHIVE" = "$ARCHIVE_DIR/backbond-scanner-$SHA.tar" ] || die 'archive path is not the fixed candidate path'
[ -f "$ARCHIVE" ] || die 'archive not found'
[ "$(sha256sum "$ARCHIVE" | awk '{print $1}')" = "$ARCHIVE_SHA" ] || die 'archive checksum mismatch'
require_root_owned "$ENV_FILE"; require_root_owned "$STATE_DIR"; require_root_owned "$UNIT_FILE"
[ "$(stat -c %a "$STATE_DIR")" = 755 ] || die 'state directory mode must be 0755'
[ "$(stat -c %a "$ENV_FILE")" = 600 ] || die 'environment file mode must be 0600'
[ "$(stat -c %a "$UNIT_FILE")" = 644 ] || die 'unit file mode must be 0644'
[ "$(grep -c '^SCANNER_EDGE_IMAGE=' "$ENV_FILE")" -le 1 ] || die 'duplicate SCANNER_EDGE_IMAGE assignment'
[ "$(grep -c '^BACKBOND_RELEASE_IDENTITY_PATH=' "$ENV_FILE")" -le 1 ] || die 'duplicate BACKBOND_RELEASE_IDENTITY_PATH assignment'
grep -Fq -- '--volume /var/lib/backbond-deploy-agent:/var/lib/backbond-deploy-agent:ro' "$UNIT_FILE" || die 'unit lacks exact read-only identity mount'

public_dns_preflight

# Reclaim before containerd ingests the new archive. No registry operation is permitted.
reclaim_deploy_space
"$DOCKER" load -i "$ARCHIVE" >/dev/null
IMAGE_ID="$("$DOCKER" image ls --no-trunc --filter "label=$REVISION_LABEL=$SHA" --format '{{.ID}}' | awk 'NF' | sort -u)"
[ "$(printf '%s\n' "$IMAGE_ID" | awk 'NF{n++} END{print n+0}')" = 1 ] || die 'expected exactly one image with requested OCI revision'
GOT_REV="$("$DOCKER" image inspect --format "{{ index .Config.Labels \"$REVISION_LABEL\" }}" "$IMAGE_ID")"
[ "$GOT_REV" = "$SHA" ] || die 'OCI revision mismatch'
MANIFEST_CONTAINER= MANIFEST_TMP=
cleanup_manifest_resources() {
  cleanup_rc=0
  if [ -n "$MANIFEST_CONTAINER" ] && ! "$DOCKER" rm -f "$MANIFEST_CONTAINER" >/dev/null 2>&1; then
    log "ERROR: persistent manifest inspection container cleanup failure: $MANIFEST_CONTAINER"
    cleanup_rc=1
  fi
  if [ -n "$MANIFEST_TMP" ] && ! rm -f -- "$MANIFEST_TMP"; then
    log "ERROR: embedded-manifest temporary-file cleanup failure: $MANIFEST_TMP"
    cleanup_rc=1
  fi
  return "$cleanup_rc"
}
trap 'rc=$?; trap - EXIT INT TERM; cleanup_rc=0; cleanup_manifest_resources || cleanup_rc=$?; [ "$rc" -ne 0 ] || rc=$cleanup_rc; exit "$rc"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
MANIFEST_TMP="$(mktemp)" || die 'cannot create embedded-manifest temporary file'
MANIFEST_CONTAINER="$("$DOCKER" create --network none "$IMAGE_ID")" || die 'cannot create manifest inspection container'
if ! "$DOCKER" cp "$MANIFEST_CONTAINER:/app/release/backbond-scanner-release.json" "$MANIFEST_TMP"; then
  die 'cannot extract embedded release manifest'
fi
if ! "$DOCKER" rm -f "$MANIFEST_CONTAINER" >/dev/null; then
  die 'cannot remove manifest inspection container'
fi
MANIFEST_CONTAINER=
if ! GOT_MANIFEST="$(sha256sum "$MANIFEST_TMP" | awk '{print $1}')"; then
  die 'cannot hash embedded release manifest'
fi
if ! python3 - "$MANIFEST_TMP" "$SHA" <<'PY'
import json,sys
p,sha=sys.argv[1:]
d=json.load(open(p,encoding='utf-8'))
expected={'scanner_version':'0.5.0','corpus_version':'0.2.0','build_sha':sha,'release_tag':'sha-'+sha}
for key,value in expected.items():
    if d.get(key)!=value: raise SystemExit(f'embedded release identity mismatch: {key}')
PY
then
  die 'embedded release identity mismatch'
fi
cleanup_manifest_resources || die 'manifest preflight resource cleanup failed'
MANIFEST_TMP=
trap - EXIT INT TERM
case $- in (*e*) :;; (*) die 'errexit disabled after manifest preflight cleanup';; esac

# Accepted scans hold LOCK_SH on this stable inode until terminal completion.
# Take LOCK_EX only after image preflight: it atomically stops new admissions
# and drains accepted credential-holding scans before activation state changes.
if [ ! -e "$ACTIVATION_LOCK" ]; then
  install -o "$EXPECTED_OWNER_UID" -g "$EXPECTED_OWNER_GID" -m 0644 /dev/null "$ACTIVATION_LOCK"
fi
require_root_owned "$ACTIVATION_LOCK"
exec 9>"$ACTIVATION_LOCK"
flock -x 9
log 'accepted scans drained; activation admission gate closed'

LOCAL_TAG="backbond-scanner-live:$SHA"
RELEASES="$STATE_DIR/releases"; TRANSACTIONS="$STATE_DIR/transactions"; RECEIPTS="$STATE_DIR/receipts"
install -d -o "$EXPECTED_OWNER_UID" -g "$EXPECTED_OWNER_GID" -m 0755 "$RELEASES" "$TRANSACTIONS" "$RECEIPTS"
TX="$(mktemp -d "$TRANSACTIONS/$SHA.XXXXXXXX")"; chmod 0700 "$TX"
CURRENT="$STATE_DIR/current_version"; IDENTITY="$RELEASES/$SHA.json"
snapshot "$ENV_FILE" env; snapshot "$CURRENT" current_version; snapshot "$IDENTITY" candidate_identity
OLD_IDENTITY_PATH="$(sed -n 's/^BACKBOND_RELEASE_IDENTITY_PATH=//p' "$ENV_FILE")"
OLD_IMAGE="$(sed -n 's/^SCANNER_EDGE_IMAGE=//p' "$ENV_FILE")"
case "$OLD_IDENTITY_PATH" in ("") :;; ("$STATE_DIR"/releases/*.json) :;; (*) die 'prior identity path escapes the release directory';; esac
if [ -n "$OLD_IDENTITY_PATH" ]; then
  require_root_owned "$OLD_IDENTITY_PATH"
  [ "$(stat -c %a "$OLD_IDENTITY_PATH")" = 444 ] || die 'prior identity mode must be 0444'
  snapshot "$OLD_IDENTITY_PATH" prior_identity
else
  : >"$TX/prior_identity.NONE"
fi
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
  if [ -n "$OLD_IDENTITY_PATH" ] && ! python3 - "$TX/prior_identity" "$TX/prior-public.json" "$TX/current_version" "$OLD_IMAGE" <<'PY'
import json,sys
identity_path,public_path,current_path,image=sys.argv[1:]
expected=json.load(open(identity_path)); observed=json.load(open(public_path))
current=open(current_path).read().strip()
required={"commit_sha":current,"current_version":current,"configured_image":image}
for key in ("candidate_id","image_digest","running_container_revision","internal_version_sha","status"):
    required[key]=expected.get(key)
for key,value in required.items():
    if value is None or observed.get(key)!=value:
        raise SystemExit(f"rollback public identity mismatch: {key}")
PY
  then
    printf 'ROLLBACK_FAILED\n' >"$TX/ROLLBACK_FAILED"; fsync_path "$TX/ROLLBACK_FAILED"; fsync_path "$TX"
    log "ROLLBACK_FAILED: prior public identity tuple mismatch; transaction retained at $TX"; exit 92
  fi
  log "rollback complete; transaction retained at $TX"; exit "$rc"
}
trap 'rollback $?' ERR INT TERM

"$DOCKER" tag "$IMAGE_ID" "$LOCAL_TAG"
RECORDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 - "$TX/identity.activating" "$SHA" "$REPO_DIGEST" "$LOCAL_TAG" "$CANDIDATE" "$RECORDED_AT" <<'PY'
import json,sys
p,sha,digest,image,candidate,at=sys.argv[1:]
v={"schema_version":"backbond-scanner-release-identity@1","compatibility_contract_version":"scanner-operations-contract@1","environment":"production","host":"srv1749248","candidate_id":candidate,"commit_sha":sha,"image_digest":digest,"configured_image":image,"running_container_revision":sha,"internal_version_sha":sha,"current_version":sha,"status":"activating","recorded_at":at}
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
for k,v in {"compatibility_contract_version":"scanner-operations-contract@1","commit_sha":sha,"current_version":sha,"running_container_revision":sha,"internal_version_sha":sha,"image_digest":digest,"configured_image":image}.items():
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
for key in ("schema_version","compatibility_contract_version","environment","host","candidate_id","commit_sha","image_digest","configured_image","running_container_revision","internal_version_sha","current_version","status"):
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
log "OK: exact Development Mode candidate activated; receipt $RECEIPTS/$SHA.json"
