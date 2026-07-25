#!/usr/bin/env bash
# Build isolated eval framework venvs for native worker execution.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENVS_DIR="${ARE_EVAL_VENVS_DIR:-/opt/are-scanner/eval-venvs}"
PYTHON="${PYTHON:-python3}"

# framework -> "requirements-file import-module distribution-name expected-version"
declare -A FRAMEWORKS=(
  [garak]="locks/garak-py312.lock garak garak 0.10.0"
  [pyrit]="locks/pyrit-py312.lock pyrit pyrit 0.6.0"
  [agentdojo]="locks/agentdojo-py312.lock agentdojo agentdojo 0.1.35"
  [inspect]="locks/inspect-py312.lock inspect_ai inspect-ai 0.3.220"
)

if [[ $# -gt 0 ]]; then
  TARGETS=("$@")
else
  TARGETS=(inspect pyrit agentdojo garak)
fi

mkdir -p "$VENVS_DIR"
rc=0

build_one() {
  local fw="$1"
  local spec="${FRAMEWORKS[$fw]:-}"
  if [[ -z "$spec" ]]; then
    echo "unknown framework: $fw (known: ${!FRAMEWORKS[*]})" >&2
    return 2
  fi
  # shellcheck disable=SC2206
  local parts=($spec)
  local req="${parts[0]}" mod="${parts[1]}" dist="${parts[2]}" want="${parts[3]}"
  local vdir="$VENVS_DIR/${fw}-venv"
  local vpy="$vdir/bin/python"

  "$PYTHON" -m venv "$vdir"
  "$vpy" -m pip install --upgrade pip >/dev/null
  "$vpy" -m pip install --require-hashes -r "$REPO_ROOT/$req"
  "$vpy" -m pip check

  # Verify both importability and the installed distribution version. Some
  # upstream modules, including agentdojo==0.1.35, do not define __version__.
  # importlib.metadata is the package-manager authority in that case.
  local got
  got="$("$vpy" -c "import ${mod}; from importlib.metadata import version; print(version('${dist}'))")"
  if [[ "$got" != "$want" ]]; then
    echo "$fw version mismatch: got '$got', expected '$want'" >&2
    return 1
  fi
  echo "$fw verified: ${mod} ${got}"
}

for fw in "${TARGETS[@]}"; do
  build_one "$fw" || rc=$?
done

exit "$rc"
