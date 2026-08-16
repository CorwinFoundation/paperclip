#!/bin/bash
CASE="$1"; EXIT_CODE="${2:-0}"
export HARNESS_DIR=/tmp/qa23768v2/harness/out/$CASE
rm -rf "$HARNESS_DIR"; mkdir -p "$HARNESS_DIR"
export HARNESS_LOG="$HARNESS_DIR/calls.log"; : > "$HARNESS_LOG"
export SCRIPTED_STDOUT_FILE=/tmp/qa23768v2/harness/cases/$CASE.txt
export SCRIPTED_EXIT="$EXIT_CODE"
export PATH=/tmp/qa23768v2/harness/bin:$PATH
export DEMO_GIT_SHA=d9d680f53db7de263f026be5284c97229126872c
set +e
bash /tmp/qa23768v2/demo-real-agent/deploy.sh /tmp/qa23768v2 >"$HARNESS_DIR/stdout.txt" 2>"$HARNESS_DIR/stderr.txt"
RC=$?
set -e
DOCKER_N=$(grep -c '^DOCKER_INVOKED' "$HARNESS_LOG" || true)
SUDO_N=$(grep -c '^SUDO_INVOKED' "$HARNESS_LOG" || true)
if [ "$DOCKER_N" -eq 0 ]; then VERDICT=BLOCKED; else VERDICT=PROCEEDED; fi
printf '%-28s rc=%-3s docker_calls=%-3s sudo_calls=%-3s %s | %s\n' \
  "$CASE" "$RC" "$DOCKER_N" "$SUDO_N" "$VERDICT" "$(head -1 "$HARNESS_DIR/stderr.txt" 2>/dev/null)"
