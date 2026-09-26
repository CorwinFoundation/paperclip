#!/usr/bin/env bash
# install-host-scripts.sh -- copy the tracked host scripts onto the box.
#
# Idempotent: re-running with no repo change is a no-op that reports "in sync".
# The repo is authoritative (see README.md). Edit here, then run this.
#
#   ops/paperclip/host-scripts/install-host-scripts.sh
#
# Staged install (no root, does not touch the live box):
#   BACKBOND_HOST_SCRIPTS_DIR=/tmp/stage ops/paperclip/host-scripts/install-host-scripts.sh
#
# Needs no root: the target is the agent user's own ~/.local/bin. Cron lines are
# NOT installed by this script -- see crontab.backbond-host for those, which the
# operator installs once with `crontab -`.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=manifest.sh
. "$here/manifest.sh"

dest="$HOST_SCRIPTS_INSTALL_DIR"
mkdir -p "$dest"

changed=0
while read -r name; do
  src="$here/$name"
  [ -f "$src" ] || { echo "MISSING-IN-REPO $name" >&2; exit 2; }
  if [ -f "$dest/$name" ] && cmp -s "$src" "$dest/$name"; then
    printf 'in sync   %s\n' "$name"
    continue
  fi
  if [ -f "$dest/$name" ]; then
    cp -p "$dest/$name" "$dest/$name.bak-preinstall"
    printf 'UPDATED   %s (previous saved as %s.bak-preinstall)\n' "$name" "$name"
  else
    printf 'INSTALLED %s\n' "$name"
  fi
  install -m 755 "$src" "$dest/$name"
  changed=$((changed + 1))
done < <(host_script_names)

# systemd --user units (BEAAA-36045). Installed 0644, not 0755: a unit is data.
# Deliberately does NOT daemon-reload or restart anything -- installing a file
# must not silently bounce a running supervisor. The hint below is the operator's
# cue, and it needs no root.
unit_dest="$HOST_USER_UNITS_INSTALL_DIR"
unit_changed=0
while read -r unit; do
  [ -n "$unit" ] || continue
  src="$here/$unit"
  [ -f "$src" ] || { echo "MISSING-IN-REPO $unit" >&2; exit 2; }
  mkdir -p "$unit_dest"
  if [ -f "$unit_dest/$unit" ] && cmp -s "$src" "$unit_dest/$unit"; then
    printf 'in sync   %s\n' "$unit"
    continue
  fi
  if [ -f "$unit_dest/$unit" ]; then
    cp -p "$unit_dest/$unit" "$unit_dest/$unit.bak-preinstall"
    printf 'UPDATED   %s (previous saved as %s.bak-preinstall)\n' "$unit" "$unit"
  else
    printf 'INSTALLED %s\n' "$unit"
  fi
  install -m 644 "$src" "$unit_dest/$unit"
  unit_changed=$((unit_changed + 1))
done < <(host_user_unit_names)

printf '\n%d file(s) changed in %s\n' "$changed" "$dest"
printf '%d unit(s) changed in %s\n' "$unit_changed" "$unit_dest"
if [ "$unit_changed" -gt 0 ]; then
  printf 'A unit changed. Apply it deliberately (no root needed):\n'
  printf '  systemctl --user daemon-reload && systemctl --user restart <unit>\n'
fi
