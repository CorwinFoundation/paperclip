#!/usr/bin/env bash
# Persist CI dependencies in the runner's named volume, outside checkout cleanup.
set -euo pipefail
test "$(id -un)" = backbond-ci
test "$HOME" = /home/backbond-ci
test "$#" = 2
kind=$1
key=$2
[[ "$key" =~ ^[a-zA-Z0-9._-]+$ ]]
case "$kind" in
  test) target=.venv ;;
  eval) target=.cache/are-eval-venvs ;;
  *) echo 'Unknown local CI cache kind.' >&2; exit 1 ;;
esac
test ! -e "$target" && test ! -L "$target"
umask 077
cache_dir=$HOME/ci-cache/$kind-$key
mkdir -p "$cache_dir" "$(dirname "$target")"
ln -s "$cache_dir" "$target"
if test "$kind" = test; then
  if test -f "$cache_dir/.ci-ready" && test -x "$cache_dir/bin/python"; then
    echo 'cache-hit=true' >> "$GITHUB_OUTPUT"
  else
    echo 'cache-hit=false' >> "$GITHUB_OUTPUT"
  fi
fi
