#!/usr/bin/env bash
# Make setup-python's cached ELF executable work without LD_LIBRARY_PATH.
# The cache is owned by the isolated runner; no root access is needed.
set -euo pipefail
test "$(id -u)" != 0
python_executable=$(readlink -f "$(command -v python)")
case "$python_executable" in
  "$RUNNER_TOOL_CACHE"/Python/*/x64/bin/python*) ;;
  *) echo 'Refusing to modify Python outside the dedicated tool cache.' >&2; exit 1 ;;
esac
current_rpath=$(patchelf --print-rpath "$python_executable")
case ":$current_rpath:" in
  *':$ORIGIN/../lib:'*) ;;
  *)
    patched_file=$(mktemp "$(dirname "$python_executable")/python-rpath.XXXXXX")
    trap 'rm -f "$patched_file"' EXIT
    cp -p "$python_executable" "$patched_file"
    patchelf --set-rpath "\$ORIGIN/../lib${current_rpath:+:$current_rpath}" "$patched_file"
    mv -f "$patched_file" "$python_executable"
    ;;
esac
env -i PATH=/usr/bin:/bin "$python_executable" -c 'import ssl, sqlite3, venv; print("Python works with a stripped environment")'
