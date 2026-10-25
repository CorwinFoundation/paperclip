#!/usr/bin/env bash
set -euo pipefail
test "$(id -u)" = 1001
test ! -e "$HOME/runner"
mkdir -p "$HOME/runner" "$HOME/toolcache"
cp -a /opt/runner/. "$HOME/runner/"
cd "$HOME/runner"
# Arguments are visible to other host UIDs through ps. The runner supports
# ACTIONS_RUNNER_INPUT_* inputs and removes them before launching job steps.
export ACTIONS_RUNNER_INPUT_JITCONFIG="$(cat /run/backbond/jit)"
exec ./run.sh
