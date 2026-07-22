#!/usr/bin/env bash
# Build isolated eval framework venvs for native worker execution.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENVS_DIR="${ARE_EVAL_VENVS_DIR:-/opt/are-scanner/eval-venvs}"
PYTHON="${PYTHON:-python3}"

declare -A FRAMEWORKS=(
  [inspect]="requirements-eval-inspect.txt inspect_ai 0.3.220"
  [pyrit]="requirements-eval-pyrit.txt pyrit 0.6.0"
  [agentdojo]="requirements-eval-agentdojo.txt agentdojo 0.1.35"
  [garak]="requirements-eval-garak.txt garak 0.10.0"
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
  local req="${parts[0]}" mod="${parts[1]}" want="${parts[2]}"
  local vdir="$VENVS_DIR/${fw}-venv"
  local vpy="$vdir/bin/python"

  "$PYTHON" -m venv "$vdir"
  "$vpy" -m pip install --upgrade pip >/dev/null
  "$vpy" -m pip install -r "$REPO_ROOT/$req"
  got="$("$vpy" -c "import ${mod}; from importlib.metadata import version; print(version('${fw}'))")"
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
