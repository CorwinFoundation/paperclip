#!/usr/bin/env bash
# check-host-script-drift.sh -- read-only. Compares each tracked host script
# against the copy installed on the box. Exits 0 when every pair matches,
# 1 when any file drifts or is absent.
#
# Costs nothing and changes nothing, so it is safe to run on any heartbeat.
# This is the enforcement half of the "repo is authoritative" rule: a tracked
# script that silently drifts from ~/.local/bin is worse than an untracked one,
# because it invites the false belief that the repo is the running code
# (BEAAA-36015).
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=manifest.sh
. "$here/manifest.sh"

dest="$HOST_SCRIPTS_INSTALL_DIR"
rc=0
while read -r name; do
  repo_sha="$(sha256sum "$here/$name" 2>/dev/null | cut -d' ' -f1)"
  if [ -z "$repo_sha" ]; then
    printf 'MISSING-IN-REPO %s\n' "$name"; rc=1; continue
  fi
  if [ ! -f "$dest/$name" ]; then
    printf 'ABSENT          %s (repo %s)\n' "$name" "${repo_sha:0:12}"; rc=1; continue
  fi
  host_sha="$(sha256sum "$dest/$name" | cut -d' ' -f1)"
  if [ "$repo_sha" = "$host_sha" ]; then
    printf 'OK              %s %s\n' "$name" "${repo_sha:0:12}"
  else
    printf 'DRIFT           %s repo=%s host=%s\n' "$name" "${repo_sha:0:12}" "${host_sha:0:12}"; rc=1
  fi
done < <(host_script_names)

# systemd --user units (BEAAA-36045). Same rule: a tracked supervisor that drifts
# from the box invites the false belief that the repo is the running config.
unit_dest="$HOST_USER_UNITS_INSTALL_DIR"
while read -r unit; do
  [ -n "$unit" ] || continue
  repo_sha="$(sha256sum "$here/$unit" 2>/dev/null | cut -d' ' -f1)"
  if [ -z "$repo_sha" ]; then
    printf 'MISSING-IN-REPO %s\n' "$unit"; rc=1; continue
  fi
  if [ ! -f "$unit_dest/$unit" ]; then
    printf 'ABSENT          %s (repo %s)\n' "$unit" "${repo_sha:0:12}"; rc=1; continue
  fi
  host_sha="$(sha256sum "$unit_dest/$unit" | cut -d' ' -f1)"
  if [ "$repo_sha" = "$host_sha" ]; then
    printf 'OK              %s %s\n' "$unit" "${repo_sha:0:12}"
  else
    printf 'DRIFT           %s repo=%s host=%s\n' "$unit" "${repo_sha:0:12}" "${host_sha:0:12}"; rc=1
  fi
done < <(host_user_unit_names)

if [ "$rc" -ne 0 ]; then
  printf '\nDrift or absence detected. Reconcile deliberately:\n'
  printf '  repo is newer  -> ops/paperclip/host-scripts/install-host-scripts.sh\n'
  printf '  host is newer  -> copy the host file into this directory, commit it, say why\n'
fi
exit "$rc"
