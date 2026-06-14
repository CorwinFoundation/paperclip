#!/usr/bin/env bash
# Install repo-tracked git hooks from .githooks/ into .git/hooks/.
# Run once after cloning the repo: `./bin/install-git-hooks.sh`
# Re-run after pulling changes to .githooks/.

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
src="$repo_root/.githooks"
dst_root="$(git rev-parse --git-common-dir)"
dst="$dst_root/hooks"

if [[ ! -d "$src" ]]; then
  echo "no .githooks/ directory at $src" >&2
  exit 1
fi

mkdir -p "$dst"
installed=0
for hook in "$src"/*; do
  [[ -f "$hook" ]] || continue
  name="$(basename "$hook")"
  install -m 0755 "$hook" "$dst/$name"
  echo "installed: $dst/$name"
  installed=$((installed + 1))
done

if [[ $installed -eq 0 ]]; then
  echo "no hook files found in $src" >&2
  exit 1
fi

echo
echo "$installed hook(s) installed. Test by making a commit on a feature branch."
