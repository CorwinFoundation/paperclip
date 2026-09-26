#!/bin/sh
# Report whether the installed scan units still match the ones in this checkout.
#
# BEAAA-30934: PR #558 landed `Environment=HOME=` and a root-prefixed
# self-heal `ExecStartPre` on the three scan services.  Fast-forwarding
# /opt/backbond/src does NOT reinstall unit files -- only `install.sh` does,
# and that needs root -- so the units under /etc/systemd/system stayed on the
# pre-#558 copy.  With `HOME` unset, `_ensure_worker_home()` falls back to
# `evidence_root/.worker-home` and dies EACCES on the 0750 parent, which is
# how the weekly Vapi scan came to exit 1 before dispatching a probe while
# both the repo and the deployed checkout already carried the fix.
#
# Nothing detected that for the whole window.  This is the detector: it is
# read-only, needs no privileges (units are 0644), and is safe to run before
# or after the root install to prove the install actually took effect.
#
# Exit status is the point: 0 = every unit installed and identical, 1 = at
# least one drifted or absent.  Deliberately not wired into any unit --
# tests/test_scheduled_scan_beaaa30240.py pins that the units invoke no
# helper of this kind, and that decision stands.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
unit_dir=${1:-/etc/systemd/system}
live_unit_dir=/etc/systemd/system

status=0
found=0

for src in "$here"/*.service "$here"/*.timer; do
  # POSIX leaves an unmatched glob as its own literal; skip that.
  test -e "$src" || continue
  found=$((found + 1))
  name=${src##*/}
  installed="$unit_dir/$name"

  if [ ! -f "$installed" ]; then
    echo "ABSENT $name"
    status=1
  elif cmp -s "$src" "$installed"; then
    echo "MATCH  $name"
  else
    echo "DRIFT  $name"
    status=1
  fi
done

test "$found" -gt 0 || { echo "no unit files beside $0" >&2; exit 1; }

# File drift and runtime health are different facts.  Only ask systemd when the
# files being compared are the live installed units: a caller pointing this
# script at a staging/tmp directory must not receive unrelated host state.
# Runtime state is reporting-only.  In particular, the Vapi timer may be
# deliberately disabled and a service's previous scan may have failed for a
# reason that should not turn an otherwise-correct install into drift.
if [ "$unit_dir" = "$live_unit_dir" ] && command -v systemctl >/dev/null 2>&1; then
  for src in "$here"/*.timer; do
    test -e "$src" || continue
    name=${src##*/}
    enabled=$(systemctl is-enabled "$name" 2>/dev/null || true)
    active=$(systemctl is-active "$name" 2>/dev/null || true)
    test -n "$enabled" || enabled=unknown
    test -n "$active" || active=unknown
    echo "TIMER  $name enabled=$enabled active=$active"
  done

  for src in "$here"/*.service; do
    test -e "$src" || continue
    name=${src##*/}
    result=$(systemctl show "$name" --property=Result --value 2>/dev/null || true)
    exec_status=$(systemctl show "$name" --property=ExecMainStatus --value 2>/dev/null || true)
    test -n "$result" || result=never-run
    test -n "$exec_status" || exec_status=unknown
    echo "SERVICE $name Result=$result ExecMainStatus=$exec_status"
  done
fi

if [ "$status" -eq 0 ]; then
  echo "All $found unit(s) in $unit_dir match this checkout."
else
  echo "Installed units are stale against this checkout; run install.sh as root." >&2
fi

exit "$status"
