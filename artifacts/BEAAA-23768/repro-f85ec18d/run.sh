#!/bin/bash
CASE="$1"; EXIT_CODE="${2:-0}"
export HARNESS_DIR=/tmp/qa3/h/out/$CASE; rm -rf "$HARNESS_DIR"; mkdir -p "$HARNESS_DIR"
export HARNESS_LOG="$HARNESS_DIR/calls.log"; : > "$HARNESS_LOG"
export SCRIPTED_STDOUT_FILE=/tmp/qa3/h/cases/$CASE.txt
export SCRIPTED_EXIT="$EXIT_CODE"
export PATH=/tmp/qa3/h/bin:$PATH
export DEMO_GIT_SHA=d9d680f53db7de263f026be5284c97229126872c
set +e
bash /tmp/qa3/demo-real-agent/deploy.sh /tmp/qa3 >"$HARNESS_DIR/stdout.txt" 2>"$HARNESS_DIR/stderr.txt"
RC=$?; set -e
D=$(grep -c "^DOCKER_INVOKED" "$HARNESS_LOG" || true); S=$(grep -c "^SUDO_INVOKED" "$HARNESS_LOG" || true)
MOUNT="-"
[ -f "$HARNESS_DIR/docker-run-argv.txt" ] && MOUNT=$(tr " " "\n" < "$HARNESS_DIR/docker-run-argv.txt" | grep "target=/run/claude-judge" || echo "no-judge-mount")
printf '%-34s rc=%s helper_calls=%s docker=%s %-9s %s\n' "$CASE" "$RC" "$S" "$D" "$([ "$D" -eq 0 ] && echo BLOCKED || echo PROCEEDED)" "$MOUNT"
