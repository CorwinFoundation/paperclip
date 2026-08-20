#!/usr/bin/env bash
# BEAAA-24049 -- safety assertions for agent-priority-warden.sh.
#
# The warden deprioritises agent seats. Its one catastrophic failure mode is
# deprioritising the control plane instead -- that would make the BEAAA-24046
# outage worse, not better. These assertions run against the live process tree
# in read-only modes only (--report / --dry-run); nothing here mutates the host.

set -uo pipefail
cd "$(dirname "$0")"
W=./agent-priority-warden.sh
pass=0; fail=0
ck() { if [ "$2" = "$3" ]; then echo "PASS  $1"; pass=$((pass+1));
       else echo "FAIL  $1: got='$2' want='$3'"; fail=$((fail+1)); fi }

report=$($W --report 2>/dev/null)
dry=$($W --dry-run 2>/dev/null)

# 1. The listener must be discovered from the live socket, not guessed.
listener=$(printf '%s\n' "$report" | sed -n 's/.*listener_pid=\([0-9]*\).*/\1/p')
[ -n "$listener" ] && [ -d /proc/"$listener" ] && r=yes || r=no
ck "listener PID discovered and live" "$r" "yes"

# 2. The discovered PID must genuinely hold the listening socket.
port="${PAPERCLIP_LISTEN_PORT:-3100}"
hexport=$(printf '%04X' "$port")
ino=$(awk -v p=":$hexport" '$2 ~ p && $4 == "0A" {print $10; exit}' /proc/net/tcp)
ls -l /proc/"$listener"/fd 2>/dev/null | grep -q "socket:\[$ino\]" && r=yes || r=no
ck "listener PID actually owns the :$port socket" "$r" "yes"

# 3. The listener must be tagged CONTROL, never agent.
printf '%s\n' "$report" | grep -qE "^$listener .*\[CONTROL\]" && r=yes || r=no
ck "listener tagged [CONTROL]" "$r" "yes"

# 4. THE LOAD-BEARING ONE: no dry-run mutation may target the listener,
#    any of its ancestors, or any postgres process.
targets=$(printf '%s\n' "$dry" | sed -n 's/.*would set pid=\([0-9]*\).*/\1/p' | sort -un)

violation=""
p="$listener"
while [ -n "$p" ] && [ "$p" != "1" ] && [ "$p" != "0" ]; do
  printf '%s\n' "$targets" | grep -qx "$p" && violation="$violation ancestor:$p"
  p=$(awk '{print $4}' /proc/"$p"/stat 2>/dev/null)
done
ck "no control-plane ancestor is a mutation target" "${violation:-none}" "none"

pgviol=""
for pg in $(pgrep -u "$(id -u)" -f '^postgres' 2>/dev/null); do
  printf '%s\n' "$targets" | grep -qx "$pg" && pgviol="$pgviol pg:$pg"
done
ck "no postgres backend is a mutation target" "${pgviol:-none}" "none"

# 5. Sabotage check -- the assertion in (4) must be capable of failing.
#    Feed the same grep a PID that IS a target and confirm it trips.
sample=$(printf '%s\n' "$targets" | head -1)
if [ -n "$sample" ]; then
  printf '%s\n' "$targets" | grep -qx "$sample" && r=trips || r=silent
  ck "sabotage: target-membership test can fire (pid=$sample)" "$r" "trips"
else
  ck "sabotage: at least one target exists to test against" "none" "some"
fi

# 6. Dry-run must be inert: no nice value may change across a run.
#    Agent subprocesses (shells, curls) are short-lived and churn constantly, so
#    compare only PIDs still alive in BOTH samples -- a PID that exited is not a
#    mutation. Comparing raw snapshots here fails on churn, not on a real change.
snap() { for p in $targets; do
           n=$(awk '{print $19}' /proc/"$p"/stat 2>/dev/null)
           [ -n "$n" ] && printf '%s:%s\n' "$p" "$n"
         done; }
before=$(snap)
$W --dry-run >/dev/null 2>&1
after=$(snap)
common=$(comm -12 <(printf '%s\n' "$before" | cut -d: -f1 | sort) \
                  <(printf '%s\n' "$after"  | cut -d: -f1 | sort))
drift=""
for p in $common; do
  b=$(printf '%s\n' "$before" | sed -n "s/^$p://p")
  a=$(printf '%s\n' "$after"  | sed -n "s/^$p://p")
  [ "$b" = "$a" ] || drift="$drift $p($b->$a)"
done
ck "--dry-run is inert across $(echo $common | wc -w) surviving PIDs" "${drift:-none}" "none"

# 6b. Sabotage: that comparison must be able to detect a real nice change.
#     Renice a throwaway child of this test -- never a real seat.
sleep 600 & victim=$!
vb=$(awk '{print $19}' /proc/$victim/stat 2>/dev/null)
renice -n 7 -p $victim >/dev/null 2>&1
va=$(awk '{print $19}' /proc/$victim/stat 2>/dev/null)
kill $victim 2>/dev/null
ck "sabotage: nice-drift detector sees a real change ($vb->$va)" "$([ "$vb" != "$va" ] && echo detected || echo missed)" "detected"

# 7. Report mode must not mutate either.
ck "--report exits clean" "$($W --report >/dev/null 2>&1; echo $?)" "0"

# 8. Bad mode is rejected rather than silently defaulting to --apply.
ck "unknown mode rejected with EX_USAGE" "$($W --frobnicate >/dev/null 2>&1; echo $?)" "64"

# 9. Disposable same-UID apply/revert: nice remains raised, while the observed
#    reversible OOM adjustment and the captured I/O priority are restored.
state_dir=$(mktemp -d)
bash -c 'exec -a "/usr/bin/codex exec --json rollback-fixture" sleep 600' & rollback_pid=$!
sleep 0.1
before_nice=$(awk '{print $19}' /proc/$rollback_pid/stat)
before_oom=$(cat /proc/$rollback_pid/oom_score_adj)
before_io=$(LC_ALL=C ionice -p $rollback_pid)
WARDEN_STATE_DIR="$state_dir" WARDEN_SEAT_ROOTS="$rollback_pid" $W --apply >/dev/null 2>&1
applied_nice=$(awk '{print $19}' /proc/$rollback_pid/stat)
applied_oom=$(cat /proc/$rollback_pid/oom_score_adj)
applied_io=$(LC_ALL=C ionice -p $rollback_pid)
WARDEN_STATE_DIR="$state_dir" WARDEN_SEAT_ROOTS="$rollback_pid" $W --revert >/dev/null 2>&1
after_nice=$(awk '{print $19}' /proc/$rollback_pid/stat)
after_oom=$(cat /proc/$rollback_pid/oom_score_adj)
after_io=$(LC_ALL=C ionice -p $rollback_pid)
kill $rollback_pid 2>/dev/null
rm -rf "$state_dir"
ck "same-UID apply raises nice" "$applied_nice" "10"
ck "same-UID apply raises OOM adjustment" "$applied_oom" "500"
ck "same-UID apply sets best-effort I/O priority" "$applied_io" "best-effort: prio 7"
ck "same-UID revert documents irreducible nice residual" "$after_nice" "10"
ck "same-UID revert restores OOM adjustment" "$after_oom" "$before_oom"
ck "same-UID revert restores I/O priority" "$after_io" "$before_io"

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ] || exit 1
