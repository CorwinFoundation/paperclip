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
ATTEMPT_LEDGER_PATH=/var/lib/are-scanner/full-scan-target-attempts.sqlite3
EXPECTED_OWNER_UID="${SCANNER_EXPECTED_OWNER_UID:-0}"
EXPECTED_OWNER_GID="${SCANNER_EXPECTED_OWNER_GID:-0}"
ARCHIVE_DIR="${SCANNER_ARCHIVE_DIR:-/var/tmp}"
ACTIVATION_LOCK="${SCANNER_ACTIVATION_LOCK_PATH:-$STATE_DIR/activation.lock}"
# BEAAA-31542: retained-evidence export. The live unit bind-mounts the state
# directory read-write at the same path on the host, so the evidence is already
# on disk; only the 0710 mode on the state directory keeps the deploy seat out.
EVIDENCE_STATE_DIR="${SCANNER_EVIDENCE_STATE_DIR:-/var/lib/are-scanner}"
EVIDENCE_EXPORT_DIR="${SCANNER_EVIDENCE_EXPORT_DIR:-/var/tmp/are-scanner-evidence-exports}"
FULL_SCAN_JOB_STORE="${SCANNER_FULL_SCAN_JOB_STORE:-$EVIDENCE_STATE_DIR/full_scan_jobs.sqlite3}"
ACCEPTED_SCAN_STORE="${SCANNER_ACCEPTED_SCAN_STORE:-$EVIDENCE_STATE_DIR/accepted_scans.sqlite3}"
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
    "$DOCKER" exec -i "$CONTAINER" sh -c \
      'unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy; exec python3 -' <<'PY_WPS_IP'
import ipaddress
import urllib.request

try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("https://api.ipify.org", timeout=10) as response:
        outbound_ip = response.read(65).decode("ascii").strip()
    print(ipaddress.ip_address(outbound_ip))
except Exception:
    raise SystemExit(1)
PY_WPS_IP
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

# BEAAA-42272: emit a read-only, scan-id-scoped projection of the durable
# full-scan record. The projection intentionally excludes tenant/agent identity,
# prompts, responses, evidence refs, credentials, and webhook configuration.
evidence_extract() {
  [ "$#" = 1 ] || die 'usage: scanner-deploy evidence-extract <scan-id>'
  scan_id=$1
  [[ "$scan_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] \
    || die 'scan id must be a lowercase uuid'
  [ ! -L "$EVIDENCE_STATE_DIR" ] || die 'evidence state directory is a symlink'
  [ ! -L "$FULL_SCAN_JOB_STORE" ] || die 'full-scan job store is a symlink'
  [ -f "$FULL_SCAN_JOB_STORE" ] || die "full-scan job store missing: $FULL_SCAN_JOB_STORE"
  python3 - "$FULL_SCAN_JOB_STORE" "$ACCEPTED_SCAN_STORE" "$scan_id" <<'PY'
import json, os, sqlite3, sys

db_path, accepted_path, scan_id = sys.argv[1:]
# Fixed field names and counts only: never print paths, keys, rows or identity.
diagnostic = {"direct_job_matches": 0, "accepted_store_present": 0,
              "accepted_scan_matches": 0, "idempotency_matches": 0,
              "correlated_job_matches": 0}
def missing():
    print(json.dumps({"schema_version": "scanner-evidence-lookup@1",
                      "counts": diagnostic}, sort_keys=True), file=sys.stderr)
    raise SystemExit(f"scan id not found: {scan_id}")
try:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    # Newer durable jobs carry scan_id directly. Query the JSON field in SQL so
    # an operator request never enumerates or loads unrelated job records.
    rows = conn.execute(
        "SELECT job_json FROM full_scan_jobs "
        "WHERE json_extract(job_json, '$.scan_id') = ?",
        (scan_id,),
    ).fetchall()
    diagnostic["direct_job_matches"] = len(rows)
    if not rows:
        # Historical jobs predate the top-level scan_id field. Resolve the
        # accepted scan exactly, then follow the durable idempotency edge that
        # the full-scan submit path wrote as tenant:scan:full-registry. Tenant
        # identity stays in-process and is never emitted in output or errors.
        if os.path.islink(accepted_path):
            raise SystemExit("accepted-scan store is a symlink")
        if not os.path.isfile(accepted_path):
            missing()
        diagnostic["accepted_store_present"] = 1
        accepted = sqlite3.connect(f"file:{accepted_path}?mode=ro", uri=True)
        try:
            accepted_rows = accepted.execute(
                "SELECT tenant_id FROM accepted_scans WHERE scan_id = ?", (scan_id,)
            ).fetchall()
        finally:
            accepted.close()
        diagnostic["accepted_scan_matches"] = len(accepted_rows)
        if len(accepted_rows) > 1:
            raise SystemExit(f"scan id is not unique: {scan_id}")
        if accepted_rows:
            correlation_key = f"{accepted_rows[0][0]}:{scan_id}:full-registry"
            diagnostic["idempotency_matches"] = conn.execute(
                "SELECT COUNT(*) FROM full_scan_idempotency WHERE idempotency_key = ?",
                (correlation_key,),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT j.job_json FROM full_scan_idempotency AS i "
                "JOIN full_scan_jobs AS j ON j.job_id = i.job_id "
                "WHERE i.idempotency_key = ?",
                (correlation_key,),
            ).fetchall()
            diagnostic["correlated_job_matches"] = len(rows)
finally:
    if "conn" in locals():
        conn.close()

matches = []
for (raw,) in rows:
    try:
        record = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid durable full-scan record: {exc}")
    matches.append(record)
if not matches:
    missing()
if len(matches) != 1:
    raise SystemExit(f"scan id is not unique: {scan_id}")

record = matches[0]
manifest = record.get("final_manifest") or {}
registry = record.get("registry_cases") or []
evidence = manifest.get("evidence_rows") or record.get("evidence_rows") or []
required_ids = sorted(str(row.get("case_id")) for row in registry
                      if row.get("required", True) and row.get("case_id"))
attempted_ids = sorted({str(row.get("probe_id")) for row in evidence if row.get("probe_id")})
terminal_statuses = {"complete", "failed", "incomplete", "skipped", "could_not_run_probe"}
terminal_ids = sorted({str(row.get("probe_id")) for row in evidence
                       if row.get("probe_id") and row.get("status") in terminal_statuses})
evidenced_ids = sorted({str(row.get("probe_id")) for row in evidence
                        if row.get("probe_id") and row.get("evidence_ref")})
incomplete_rows = [
    {"case_id": str(row.get("probe_id")),
     "status": row.get("status"),
     "incomplete_reason": row.get("incomplete_reason")}
    for row in evidence
    if row.get("probe_id") and (row.get("status") != "complete" or row.get("incomplete_reason"))
]
incomplete_rows.sort(key=lambda row: row["case_id"])
reconciliation = manifest.get("reconciliation") or {}
output = {
    "schema_version": "scanner-evidence-extract@1",
    "scan_id": scan_id,
    "job_id": record.get("job_id"),
    "job_status": record.get("status"),
    "registry": {
        "scanner_version": record.get("scanner_version"),
        "corpus_version": record.get("corpus_version"),
        "snapshot_hash": manifest.get("registry_snapshot_hash"),
    },
    "expected_required": {"count": len(required_ids), "case_ids": required_ids},
    "attempted": {"count": int(reconciliation.get("attempted", len(attempted_ids))), "case_ids": attempted_ids},
    "terminal": {"count": int(reconciliation.get("terminal", len(terminal_ids))), "case_ids": terminal_ids},
    "evidenced": {"count": int(reconciliation.get("evidenced", len(evidenced_ids))), "case_ids": evidenced_ids},
    "incomplete_rows": incomplete_rows,
    "incomplete_reasons": list(manifest.get("incomplete_reasons") or []),
    "bind_eligibility": manifest.get("bind_eligibility", record.get("bind_eligibility")),
    "errors": list(manifest.get("errors") or []),
    "final_manifest_hash": record.get("final_manifest_hash") or manifest.get("manifest_hash"),
}
print(json.dumps(output, indent=2, sort_keys=True))
PY
}

if [ "${1:-}" = evidence-extract ]; then
  shift
  evidence_extract "$@"
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

# BEAAA-42359: idempotent renewal of the two fixed BEAAA-38869 QA tenant keys
# (qa-38869-a, qa-38869-b). A key that still resolves live for its tenant with
# at least a day left is kept; an expired, revoked, unknown or near-expiry key
# is replaced by a fresh seven-day key and the old key_id revoked. Tokens move
# only over stdin pipes and land 0600 in the owner's file; stdout carries
# key_id/expiry only. Tenants, TTL and file path are fixed here, not arguments.
QA_KEYS_CONTAINER_PROGRAM='
import os, sys, time
from api.edge_gateway import KeyStore
tenant, key_id, ttl, min_remaining = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
current = sys.stdin.readline().strip()
fresh = sys.stdin.readline().strip()
store = KeyStore(os.environ["ARE_KEY_STORE_PATH"])
old = store.resolve(current) if current else None
if old is not None and old.tenant_id == tenant and old.expires_at - time.time() >= min_remaining:
    print(f"kept key_id={old.key_id} expires_at={int(old.expires_at)}")
    raise SystemExit(0)
if not fresh.startswith("bbqa_") or len(fresh) != 53:
    raise SystemExit("fresh token has the wrong shape")
rec = store.issue(token=fresh, key_id=key_id, tenant_id=tenant, ttl_seconds=ttl, scopes=None)
if old is not None and old.key_id != key_id:
    store.revoke(old.key_id)
print(f"renewed key_id={rec.key_id} expires_at={int(rec.expires_at)}")
raise SystemExit(10)
'
qa_tenant_keys() {
  [ "$#" = 1 ] && [ "$1" = beaaa38869 ] || die 'usage: scanner-deploy qa-tenant-keys beaaa38869'
  qa_owner="${QA_TOKENS_OWNER:-are-deploy}"
  qa_out="${QA_TOKENS_FILE:-/home/are-deploy/qa-38869.tokens}"
  qa_uid="$(id -u "$qa_owner")" || die "unknown token owner: $qa_owner"
  qa_gid="$(id -g "$qa_owner")" || die "unknown token owner: $qa_owner"
  "$DOCKER" exec "$CONTAINER" sh -c 'test -n "${ARE_KEY_STORE_PATH:-}"' \
    || die "ARE_KEY_STORE_PATH is not set in $CONTAINER; edge gateway key store not in use"
  QA_DOCKER="$DOCKER" QA_CONTAINER="$CONTAINER" QA_OUT="$qa_out" QA_UID="$qa_uid" QA_GID="$qa_gid" \
    QA_PROGRAM="$QA_KEYS_CONTAINER_PROGRAM" QA_MIN_REMAINING="${QA_RENEW_MIN_REMAINING_SECONDS:-86400}" \
    python3 - <<'PY'
import os, secrets, stat, subprocess, sys, time

TENANTS = ("qa-38869-a", "qa-38869-b")
TTL = 604800  # seven days; changing it needs a Founder decision (BEAAA-38869)
out, uid, gid = os.environ["QA_OUT"], int(os.environ["QA_UID"]), int(os.environ["QA_GID"])
directory = os.path.dirname(out) or "."


def read_tokens():
    try:
        fd = os.open(out, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {}
    except OSError:
        sys.exit(f"[scanner-deploy] ERROR: refusing token file {out}: not a plain file")
    with os.fdopen(fd, "r") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != uid or st.st_nlink != 1:
            sys.exit(f"[scanner-deploy] ERROR: refusing token file {out}: wrong type, owner or link count")
        tokens = {}
        for line in fh.read(65536).splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] in TENANTS:
                tokens[parts[0]] = parts[1]
        return tokens


def write_tokens(tokens):
    tmp = os.path.join(directory, f".qa-38869.tokens.{secrets.token_hex(8)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, 0o600)
        os.write(fd, "".join(f"{t} {tokens[t]}\n" for t in TENANTS if t in tokens).encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(tmp, out)


tokens = read_tokens()
for tenant in TENANTS:
    key_id = f"{tenant}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    fresh = "bbqa_" + secrets.token_hex(24)
    proc = subprocess.run(
        [os.environ["QA_DOCKER"], "exec", "-i", os.environ["QA_CONTAINER"], "python", "-c",
         os.environ["QA_PROGRAM"], tenant, key_id, str(TTL), os.environ["QA_MIN_REMAINING"]],
        input=f"{tokens.get(tenant, '')}\n{fresh}\n", capture_output=True, text=True,
    )
    status = proc.stdout.strip().splitlines()[-1:] or [""]
    if proc.returncode == 10 and status[0].startswith("renewed "):
        tokens[tenant] = fresh
        write_tokens(tokens)
    elif proc.returncode != 0 or not status[0].startswith("kept "):
        detail = (proc.stderr.strip().splitlines() or [""])[-1][:200]
        sys.exit(f"[scanner-deploy] ERROR: {tenant} key check failed (exit {proc.returncode}): {detail}")
    print(f"{tenant} {status[0]}")
print(f"tokens in {out}")
PY
}

if [ "${1:-}" = qa-tenant-keys ]; then
  shift
  qa_tenant_keys "$@"
  exit 0
fi

# BEAAA-42967: one controlled out-of-band restart of the live container while
# a single credential-free fixture scan is in flight, to exercise the startup
# recovery path (resume_accepted_scans). A deploy drains accepted scans behind
# the activation lock, so it never reaches that path. This restart deliberately
# does not take the activation lock, so it refuses unless the only unfinished
# work in both durable stores is the named fixture scan. Prints ids/counts only.
QA_FIXTURE_TENANT=qa-38869-a
QA_FIXTURE_URL_PREFIX="$PUBLIC_BASE/qa-restart-fixture/v1/"
qa_restart_guard() {
  python3 - "$ACCEPTED_SCAN_STORE" "$FULL_SCAN_JOB_STORE" "$1" "$QA_FIXTURE_TENANT" "$QA_FIXTURE_URL_PREFIX" <<'PY'
import json, os, sqlite3, sys
accepted_path, jobs_path, scan_id, tenant, prefix = sys.argv[1:]
for p in (accepted_path, jobs_path):
    if os.path.islink(p) or not os.path.isfile(p):
        raise SystemExit(f"durable store missing or a symlink: {os.path.basename(p)}")
def ro(p):
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)
conn = ro(accepted_path)
try:
    rows = conn.execute(
        "SELECT scan_id, tenant_id, state, resumable, resume_count, completed_test_count, request_json "
        "FROM accepted_scans WHERE state != 'terminal'").fetchall()
finally:
    conn.close()
others = [r for r in rows if r[0] != scan_id]
mine = [r for r in rows if r[0] == scan_id]
if others:
    raise SystemExit(f"refusing restart: {len(others)} other unfinished accepted scan(s)")
if len(mine) != 1:
    raise SystemExit("refusing restart: fixture scan is not unfinished")
_, row_tenant, state, resumable, resume_count, done, request_json = mine[0]
try:
    url = json.loads(request_json).get("agent_endpoint")
except ValueError:
    url = None
url = url if isinstance(url, str) else ""
if row_tenant != tenant or not url.startswith(prefix) or resumable != 1:
    raise SystemExit("refusing restart: scan is not a credential-free fixture scan")
conn = ro(jobs_path)
try:
    jobs = conn.execute(
        "SELECT json_extract(job_json, '$.scan_id') FROM full_scan_jobs "
        "WHERE status NOT IN ('complete','incomplete','failed','cancelled','incomplete_cost_cap')").fetchall()
finally:
    conn.close()
foreign = [j for j in jobs if j[0] != scan_id]
if foreign:
    raise SystemExit(f"refusing restart: {len(foreign)} other unfinished full-scan job(s)")
print(json.dumps({"scan_id": scan_id, "state": state, "resumable": resumable,
                  "resume_count": resume_count, "completed_test_count": done,
                  "unfinished_full_scan_jobs": len(jobs)}, sort_keys=True))
PY
}
qa_restart_test() {
  [ "$#" = 1 ] || die 'usage: scanner-deploy qa-restart-fixture restart-test <scan-id>'
  [[ "$1" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || die 'scan id must be a lowercase uuid'
  pre="$(qa_restart_guard "$1")" || die 'restart-test guard refused; nothing was restarted'
  log "pre-restart $pre"
  started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log "restart-test: $SYSTEMCTL restart $SERVICE at $started"
  "$SYSTEMCTL" restart "$SERVICE"
  "$SYSTEMCTL" is-active --quiet "$SERVICE" || die 'service is not active after restart'
  wait_200 "$LOCAL_URL" || die 'scanner did not become ready after restart'
  log "restart-test: ready at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  # Startup recovery evidence: the fixture row after the sweep, and only the
  # accepted_scan_* recovery tokens from the new container's log.
  log "post-restart $(python3 - "$ACCEPTED_SCAN_STORE" "$1" <<'PY'
import json, sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
row = conn.execute("SELECT state, resumable, resume_count, completed_test_count FROM accepted_scans "
                   "WHERE scan_id=?", (sys.argv[2],)).fetchone()
conn.close()
keys = ("state", "resumable", "resume_count", "completed_test_count")
print(json.dumps(dict(zip(keys, row)) if row else None, sort_keys=True))
PY
)"
  "$DOCKER" logs --since "$started" "$CONTAINER" 2>&1 \
    | grep -oE 'accepted_scan_(resumed|restart_failed) scan_id=[0-9a-f-]{36}( reason=[a-z_]+)?' \
    | head -n 20 | sed 's/^/startup-recovery: /' >&2 || true
}

# BEAAA-42388: credential-free restart test target + signed callback receiver
# for the BEAAA-38869 item 2 live check. The installer is taken from the live
# image (like scan-intake) so the code that runs is the code master deployed.
# install is idempotent and only adds the dedicated qa-38869-callback ref;
# remove drops only this fixture and that ref, keeping receipts and evidence.
qa_restart_fixture() {
  case "${1:-}" in
    install|status|receipts|remove) ;;
    restart-test) shift; qa_restart_test "$@"; return ;;
    *) die 'usage: scanner-deploy qa-restart-fixture install [--delay-seconds N] | status | receipts | remove | restart-test <scan-id>' ;;
  esac
  work="$(mktemp -d)" || die 'cannot create qa-restart-fixture staging directory'
  trap 'rm -rf -- "$work"' RETURN
  "$DOCKER" cp "$CONTAINER:/app/backbond/qa-restart-fixture/." "$work/" \
    || die 'cannot extract qa-restart-fixture payload from live image'
  [ -f "$work/install.py" ] && [ -f "$work/fixture.py" ] || die 'qa-restart-fixture payload is incomplete'
  SCANNER_EDGE_ENV="$ENV_FILE" python3 "$work/install.py" "$@"
}

if [ "${1:-}" = qa-restart-fixture ]; then
  shift
  qa_restart_fixture "$@"
  exit 0
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

[ "$#" = 5 ] || die 'usage: scanner-deploy <commit-sha> <archive.tar> <repository@sha256:digest> <archive-sha256> <candidate-id> | scanner-deploy refresh-helper <commit-sha> <staged-helper> <helper-sha256> | scanner-deploy preflight | scanner-deploy scan-intake <commit-sha> | scanner-deploy export-evidence [job <job-id>] | scanner-deploy evidence-extract <scan-id> | scanner-deploy disk-maintenance [--apply] | scanner-deploy acceptance starter-v5 <commit-prefix> | scanner-deploy qa-restart-fixture install|status|receipts|remove|restart-test <scan-id> | scanner-deploy qa-tenant-keys beaaa38869'
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

awk '!/^SCANNER_EDGE_IMAGE=/ && !/^BACKBOND_RELEASE_IDENTITY_PATH=/ && !/^ARE_FULL_SCAN_ATTEMPT_LEDGER_PATH=/' "$ENV_FILE" >"$TX/env.next"
printf 'SCANNER_EDGE_IMAGE=%s\nBACKBOND_RELEASE_IDENTITY_PATH=%s\nARE_FULL_SCAN_ATTEMPT_LEDGER_PATH=%s\n' "$LOCAL_TAG" "$IDENTITY" "$ATTEMPT_LEDGER_PATH" >>"$TX/env.next"
[ "$(grep -c '^SCANNER_EDGE_IMAGE=' "$TX/env.next")" = 1 ]
[ "$(grep -c '^BACKBOND_RELEASE_IDENTITY_PATH=' "$TX/env.next")" = 1 ]
[ "$(grep -c '^ARE_FULL_SCAN_ATTEMPT_LEDGER_PATH=' "$TX/env.next")" = 1 ]
printf '%s\n' "$SHA" >"$TX/current.next"
atomic_install "$TX/current.next" "$CURRENT" 0644
atomic_install "$TX/env.next" "$ENV_FILE" 0600
"$SYSTEMCTL" restart "$SERVICE"
"$SYSTEMCTL" is-active --quiet "$SERVICE"
wait_200 "$LOCAL_URL"
[ "$(container_env_value ARE_FULL_SCAN_ATTEMPT_LEDGER_PATH)" = "$ATTEMPT_LEDGER_PATH" ] || die 'running container lacks the durable target-attempt ledger path'
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
