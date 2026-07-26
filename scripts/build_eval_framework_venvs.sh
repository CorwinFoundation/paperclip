#!/usr/bin/env bash
# Build the per-framework offline eval venvs (BEAAA-13331).
#
# The full eval-framework release contract in requirements.txt is NOT
# installable in one interpreter: garak==0.10.0 (datasets<2.17) is mutually
# exclusive with pyrit==0.6.0 (datasets>=3.0.0). This recipe materializes each
# conflicting framework in its OWN venv so the whole contract is materializable
# for offline N=5 calibration / CI without asking pip to solve an impossible
# graph.
#
# Each venv is built from its per-framework slice:
#   garak      -> requirements-eval-garak.txt      -> <root>/garak-venv
#   pyrit      -> requirements-eval-pyrit.txt       -> <root>/pyrit-venv
#   agentdojo  -> requirements-eval-agentdojo.txt   -> <root>/agentdojo-venv
#   inspect    -> requirements-eval-inspect.txt     -> <root>/inspect-venv
#
# The soft-import runners (scanners/{pyrit,agentdojo,inspect}_runner.py) resolve
# availability + pinned version from these venvs via
# scanners.eval_framework_venv.resolve_framework. This does NOT touch the
# production runtime slice (BEAAA-13329) or any scoring/rubric/corpus.
#
# Usage:
#   scripts/build_eval_framework_venvs.sh                 # build all four
#   scripts/build_eval_framework_venvs.sh garak pyrit     # build a subset
#   ARE_EVAL_VENVS_DIR=/tmp/eval-venvs scripts/build_eval_framework_venvs.sh
#
# Env:
#   ARE_EVAL_VENVS_DIR  root for the venvs (default /opt/are-scanner/eval-venvs)
#   PYTHON              base interpreter used to create venvs (default python3)
#   TRANSCRIPT_DIR      write per-framework install and pip-check transcripts
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENVS_DIR="${ARE_EVAL_VENVS_DIR:-/opt/are-scanner/eval-venvs}"
PYTHON="${PYTHON:-python3}"
TRANSCRIPT_DIR="${TRANSCRIPT_DIR:-$VENVS_DIR/transcripts}"

# framework -> "requirements-file import-module distribution-name expected-version"
declare -A FRAMEWORKS=(
  [garak]="requirements-eval-garak.txt garak garak 0.10.0"
  [pyrit]="requirements-eval-pyrit.txt pyrit pyrit 0.6.0"
  [agentdojo]="requirements-eval-agentdojo.txt agentdojo agentdojo 0.1.35"
  [inspect]="requirements-eval-inspect.txt inspect_ai inspect-ai 0.3.220"
)

if [[ $# -gt 0 ]]; then
  TARGETS=("$@")
else
  TARGETS=(garak pyrit agentdojo inspect)
fi

echo "== build_eval_framework_venvs (BEAAA-13331) =="
echo "   repo:   $REPO_ROOT"
echo "   root:   $VENVS_DIR"
echo "   python: $($PYTHON --version 2>&1)"
mkdir -p "$VENVS_DIR"
mkdir -p "$TRANSCRIPT_DIR"

build_one() {
  local fw="$1"
  local spec="${FRAMEWORKS[$fw]:-}"
  if [[ -z "$spec" ]]; then
    echo "!! unknown framework: $fw (known: ${!FRAMEWORKS[*]})" >&2
    return 2
  fi
  # shellcheck disable=SC2206
  local parts=($spec)
  local req="${parts[0]}" mod="${parts[1]}" dist="${parts[2]}" want="${parts[3]}"
  local vdir="$VENVS_DIR/${fw}-venv"
  local vpy="$vdir/bin/python"

  echo
  echo "-- $fw --"
  echo "   slice:  $req"
  echo "   venv:   $vdir"

  "$PYTHON" -m venv --clear "$vdir"
  "$vpy" -m pip install --require-hashes -r "$REPO_ROOT/$req" \
    2>&1 | tee "$TRANSCRIPT_DIR/${fw}-install.log"

  # Verify both importability and the installed distribution version. Some
  # upstream modules, including agentdojo==0.1.35, do not define __version__.
  # importlib.metadata is the package-manager authority in that case.
  local got
  got="$("$vpy" -c "import ${mod}; from importlib.metadata import version; print(version('${dist}'))")"
  if [[ "$got" != "$want" ]]; then
    echo "!! $fw version mismatch: got '$got', expected '$want'" >&2
    return 1
  fi
  echo "   verified: ${mod} ${got}"

  "$vpy" -m pip check 2>&1 | tee "$TRANSCRIPT_DIR/${fw}-pip-check.log"
}

rc=0
for fw in "${TARGETS[@]}"; do
  build_one "$fw" || rc=$?
done

echo
if [[ $rc -eq 0 ]]; then
  echo "== all requested eval venvs built + verified =="
  echo "   confirm: ARE_EVAL_VENVS_DIR=$VENVS_DIR $PYTHON -m scanners.eval_framework_venv"
else
  echo "== FAILED (rc=$rc) — see errors above ==" >&2
fi
exit $rc
