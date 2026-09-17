#!/bin/sh
# Make the existing scan run directories readable after a fresh install.
#
# install.sh chowns $manifest_root itself but not its contents.  The run
# directories under it were written before the installer existed and carry
# whatever uid and mode wrote them, so a non-recursive chown leaves the root
# traversable with the evidence inside it still unreadable.  That would make
# the single root install run insufficient and cost a second one.
#
# The glob is deliberate: POSIX `*` skips dotfiles, which keeps
# $manifest_root/.worker-home out of this.  That is a HOME directory and must
# not become world-readable.  Evidence under the run directories is already
# declared world-readable by install.sh's `chmod 0755 "$manifest_root"`; this
# extends that decision to what is inside them rather than making a new one.
set -eu

manifest_root=${1:?usage: relax-run-dir-perms.sh MANIFEST_ROOT UNIT_USER}
unit_user=${2:?usage: relax-run-dir-perms.sh MANIFEST_ROOT UNIT_USER}

test -d "$manifest_root" || { echo "not a directory: $manifest_root" >&2; exit 1; }

for run_dir in "$manifest_root"/*; do
  test -e "$run_dir" || continue
  chown -R "$unit_user:$unit_user" "$run_dir"
  chmod -R a+rX "$run_dir"
done
