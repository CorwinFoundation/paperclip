#!/bin/sh
# Install the scan unit files, and nothing else.
#
# BEAAA-30934: `install.sh` is the only path that puts a unit file under
# /etc/systemd/system, but it is not a unit installer -- it also clears and
# rebuilds /opt/backbond/scan-venv with `python3.11 -m venv --clear`, rebuilds
# the Garak eval venvs, rewrites /etc/sudoers.d/backbond-scan-schedule
# wholesale, and enables both timers.  A pip failure part-way through leaves
# the venv every scan depends on in pieces, and the zoo timer comes back on
# whether or not it was deliberately switched off.
#
# That blast radius is the wrong price for the thing actually needed: the
# installed units drifted from the checkout at PR #558 and the weekly Vapi
# scan has been exiting 1 before dispatching a probe ever since, because
# `HOME` is unset and `_ensure_worker_home()` dies EACCES on the 0750 parent.
# Refreshing the unit files fixes that.  Nothing else in install.sh does.
#
# So this script installs unit files, validates and drops in the one sudoers
# grant the BEAAA-30934 replay unit needs, reloads systemd, and stops.  It
# builds no venv, touches no existing sudoers file, and enables and disables
# no timer -- enablement is a .wants symlink and is left exactly as found.
# It ends by running check-unit-drift.sh so the run proves its own effect.
#
# Usage:  install-units-only.sh [unit_dir] [sudoers_dir]
# Root is required only for the real /etc/systemd/system, so the test suite
# can point it at a temp directory unprivileged -- the same arrangement
# check-unit-drift.sh uses.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
default_unit_dir=/etc/systemd/system
unit_dir=${1:-$default_unit_dir}
sudoers_dir=${2:-/etc/sudoers.d}
sudoers_file=$sudoers_dir/backbond-scan-beaaa30934
replay_unit=backbond-scan-vapi-beaaa30934.service
zoo_diagnostic_sudoers_file=$sudoers_dir/backbond-scan-beaaa39322
zoo_diagnostic_unit=backbond-scan-zoo-beaaa39322.service
zoo_oosc_sudoers_file=$sudoers_dir/backbond-scan-beaaa41227
zoo_oosc_unit=backbond-scan-zoo-beaaa41227.service

if [ "$unit_dir" = "$default_unit_dir" ]; then
  test "$(id -u)" -eq 0 || { echo 'run as root' >&2; exit 1; }
fi

# Ownership flags only make sense as root; the unprivileged test run still
# exercises every other step.
_install_file() {
  if [ "$(id -u)" -eq 0 ]; then
    install -o root -g root -m "$3" "$1" "$2"
  else
    install -m "$3" "$1" "$2"
  fi
}

test -f "$here/$replay_unit" || { echo "missing $replay_unit beside $0" >&2; exit 1; }
test -f "$here/$zoo_diagnostic_unit" || { echo "missing $zoo_diagnostic_unit beside $0" >&2; exit 1; }
test -f "$here/$zoo_oosc_unit" || { echo "missing $zoo_oosc_unit beside $0" >&2; exit 1; }

found=0
for src in "$here"/*.service "$here"/*.timer; do
  # POSIX leaves an unmatched glob as its own literal; skip that.
  test -e "$src" || continue
  found=$((found + 1))
  _install_file "$src" "$unit_dir/${src##*/}" 0644
done
test "$found" -gt 0 || { echo "no unit files beside $0" >&2; exit 1; }
echo "Installed $found unit file(s) into $unit_dir."

# A malformed sudoers file locks the box out of sudo, so build it somewhere
# harmless, syntax-check it, and only then move it into place.
visudo_bin=$(command -v visudo || true)
if [ -z "$visudo_bin" ] && [ -x /usr/sbin/visudo ]; then
  visudo_bin=/usr/sbin/visudo
fi
staged=$(mktemp)
trap 'rm -f "$staged"' EXIT
printf '%s\n' \
  "beai-agent ALL=(root) NOPASSWD: /usr/bin/systemctl start $replay_unit, /usr/bin/systemctl status $replay_unit" \
  > "$staged"
if [ -n "$visudo_bin" ] && [ -x "$visudo_bin" ]; then
  "$visudo_bin" -cf "$staged" >/dev/null
elif [ "$(id -u)" -eq 0 ]; then
  echo 'visudo not found; refusing to install an unvalidated sudoers file' >&2
  exit 1
else
  echo 'visudo not found; skipping validation (unprivileged run)' >&2
fi
_install_file "$staged" "$sudoers_file" 0440
echo "Installed sudoers drop-in $sudoers_file (start/status on $replay_unit only)."

printf '%s\n' \
  "beai-agent ALL=(root) NOPASSWD: /usr/bin/systemctl start $zoo_diagnostic_unit, /usr/bin/systemctl status $zoo_diagnostic_unit" \
  > "$staged"
if [ -n "$visudo_bin" ] && [ -x "$visudo_bin" ]; then
  "$visudo_bin" -cf "$staged" >/dev/null
fi
_install_file "$staged" "$zoo_diagnostic_sudoers_file" 0440
echo "Installed sudoers drop-in $zoo_diagnostic_sudoers_file (start/status on $zoo_diagnostic_unit only)."

printf '%s\n' \
  "beai-agent ALL=(root) NOPASSWD: /usr/bin/systemctl start $zoo_oosc_unit, /usr/bin/systemctl status $zoo_oosc_unit" \
  > "$staged"
if [ -n "$visudo_bin" ] && [ -x "$visudo_bin" ]; then
  "$visudo_bin" -cf "$staged" >/dev/null
fi
_install_file "$staged" "$zoo_oosc_sudoers_file" 0440
echo "Installed sudoers drop-in $zoo_oosc_sudoers_file (start/status on $zoo_oosc_unit only)."

if [ "$unit_dir" = "$default_unit_dir" ]; then
  systemctl daemon-reload
  echo 'Reloaded systemd. No timer was enabled or disabled.'
fi

# Prove the install took, rather than asserting it.
"$here/check-unit-drift.sh" "$unit_dir"
