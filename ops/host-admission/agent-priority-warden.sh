#!/usr/bin/env bash
# BEAAA-24049 -- unprivileged priority isolation for agent seats.
#
# The clean fix is a cgroup split (Delegate=yes + a capped agents subtree); that
# needs root, which this account does not have -- `sudo -n true` returns "a
# password is required", uid is 1001(beai-agent). See systemd/ for the staged
# root-required drop-in and who must apply it.
#
# This script is the part that works without root. Every agent seat runs as the
# same uid as this script, and a process may always renice its uid-peers
# *downward* in priority and raise their oom_score_adj. That is enough to fix
# the most dangerous property of the current setup:
#
#   Today the 474 MiB control-plane node process and a 400 MiB agent seat have
#   identical oom_score_adj (0). Under memory pressure the kernel picks by
#   badness, which is roughly proportional to RSS -- so the listener is a
#   *prime* OOM candidate, ranked alongside the very workloads that starved it.
#   Combined with OOMPolicy=stop on the unit, one OOM kill takes down the whole
#   control plane. This script makes agent seats strictly more attractive to the
#   OOM killer than the control plane, inverting that ordering.
#
# It does not cap memory. Only cgroups or admission control can do that; this is
# damage-limitation for when the cap is absent or breached.
#
# Default mode is --dry-run: prints the plan, changes nothing. Live mutation of
# this shared host requires the QA and Founder gates named on BEAAA-24049.

set -uo pipefail

MODE="${1:---dry-run}"
AGENT_NICE="${AGENT_NICE:-10}"
AGENT_OOM_ADJ="${AGENT_OOM_ADJ:-500}"
PORT="${PAPERCLIP_LISTEN_PORT:-3100}"
STATE_DIR="${WARDEN_STATE_DIR:-${XDG_RUNTIME_DIR:-/tmp}/backbond-agent-priority-warden-$(id -u)}"

case "$MODE" in
  --dry-run|--apply|--revert|--report) ;;
  *) echo "usage: $0 [--dry-run|--apply|--revert|--report]" >&2; exit 64 ;;
esac

# ---- identify the control plane: never touch these -------------------------
# The listener PID is authoritative; walk up to its ancestors and add every
# postgres process in the same unit. Anything here is protected.
hexport=$(printf '%04X' "$PORT")
inode=$(awk -v p=":$hexport" '$2 ~ p && $4 == "0A" {print $10; exit}' /proc/net/tcp 2>/dev/null)
listener=""
if [ -n "${inode:-}" ]; then
  for pid in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
    if ls -l /proc/"$pid"/fd 2>/dev/null | grep -q "socket:\[$inode\]"; then listener="$pid"; break; fi
  done
fi

protected=""
add_protected() { case " $protected " in *" $1 "*) ;; *) protected="$protected $1" ;; esac; }

if [ -n "$listener" ]; then
  p="$listener"
  while [ -n "$p" ] && [ "$p" != "1" ] && [ "$p" != "0" ]; do
    add_protected "$p"
    p=$(awk '{print $4}' /proc/"$p"/stat 2>/dev/null)
  done
fi
for p in $(pgrep -u "$(id -u)" -f '^postgres' 2>/dev/null); do add_protected "$p"; done
for p in $(pgrep -u "$(id -u)" -f 'corepack pnpm|tsx src/index.ts' 2>/dev/null); do add_protected "$p"; done

is_protected() { case " $protected " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

# ---- identify agent seats and their descendant trees ------------------------
# WARDEN_SEAT_ROOTS is a test-only injection used to constrain mutation to a
# disposable process. Production callers leave it unset and use discovery.
if [ -n "${WARDEN_SEAT_ROOTS:-}" ]; then
  seat_roots=$(printf '%s\n' "$WARDEN_SEAT_ROOTS" | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -un)
else
  seat_roots=$(pgrep -u "$(id -u)" -f 'local/bin/claude --print|/usr/bin/codex exec --json' 2>/dev/null | sort -un)
fi

descendants() { # emit pid + all descendants, breadth-first
  local queue="$1" out="" cur kids
  while [ -n "$queue" ]; do
    cur="${queue%% *}"; queue="${queue#"$cur"}"; queue="${queue# }"
    case " $out " in *" $cur "*) continue ;; esac
    out="$out $cur"
    kids=$(pgrep -P "$cur" 2>/dev/null | tr '\n' ' ')
    [ -n "$kids" ] && queue="$queue $kids"
  done
  echo "$out"
}

targets=""
for r in $seat_roots; do
  for d in $(descendants "$r"); do
    is_protected "$d" && continue
    case " $targets " in *" $d "*) ;; *) targets="$targets $d" ;; esac
  done
done

# ---- act -------------------------------------------------------------------
n_ok=0; n_skip=0; n_fail=0
want_nice="$AGENT_NICE"; want_oom="$AGENT_OOM_ADJ"
if [ "$MODE" = "--revert" ]; then want_nice=0; want_oom=0; fi

start_time() { awk '{print $22}' /proc/"$1"/stat 2>/dev/null; }

read_ioprio() {
  local value
  value=$(LC_ALL=C ionice -p "$1" 2>/dev/null) || return 1
  case "$value" in
    "none: prio "*) printf '0 %s\n' "${value##* }" ;;
    "realtime: prio "*) printf '1 %s\n' "${value##* }" ;;
    "best-effort: prio "*) printf '2 %s\n' "${value##* }" ;;
    idle) printf '3 0\n' ;;
    *) return 1 ;;
  esac
}

capture_state() {
  local p="$1" file="$STATE_DIR/$1.state" io tmp
  if [ -r "$file" ]; then
    read -r recorded_start _ < "$file"
    [ "$recorded_start" = "$(start_time "$p")" ] && return 0
    rm -f "$file" || return 1
  fi
  io=$(read_ioprio "$p") || return 1
  [ ! -L "$STATE_DIR" ] || return 1
  mkdir -p "$STATE_DIR" || return 1
  chmod 700 "$STATE_DIR" || return 1
  [ "$(stat -c %u "$STATE_DIR" 2>/dev/null)" = "$(id -u)" ] || return 1
  tmp="$file.$$"
  (umask 077; printf '%s %s %s %s\n' "$(start_time "$p")" \
    "$(awk '{print $19}' /proc/"$p"/stat)" \
    "$(cat /proc/"$p"/oom_score_adj)" "$io" > "$tmp") || return 1
  mv -n "$tmp" "$file" 2>/dev/null || rm -f "$tmp"
}

printf 'mode=%s listener_pid=%s protected=%s seat_roots=%s targets=%s\n' \
  "$MODE" "${listener:-unknown}" "$(echo $protected | wc -w)" "$(echo $seat_roots | wc -w)" "$(echo $targets | wc -w)"

if [ "$MODE" = "--report" ]; then
  printf '\n%-9s %-5s %-6s %-9s %s\n' PID NICE OOMADJ RSS_MB CMD
  for p in $listener $targets; do
    [ -d /proc/"$p" ] || continue
    ni=$(awk '{print $19}' /proc/"$p"/stat 2>/dev/null)
    oa=$(cat /proc/"$p"/oom_score_adj 2>/dev/null)
    rs=$(awk '/VmRSS/{print int($2/1024)}' /proc/"$p"/status 2>/dev/null)
    cm=$(tr '\0\n\t' '   ' < /proc/"$p"/cmdline 2>/dev/null | cut -c1-58)
    tag=$(is_protected "$p" && echo "[CONTROL]" || echo "[agent]")
    printf '%-9s %-5s %-6s %-9s %s %s\n' "$p" "${ni:-?}" "${oa:-?}" "${rs:-?}" "$tag" "$cm"
  done
  exit 0
fi

for p in $targets; do
  [ -d /proc/"$p" ] || { n_skip=$((n_skip+1)); continue; }
  if is_protected "$p"; then n_skip=$((n_skip+1)); continue; fi   # belt and braces
  cur_ni=$(awk '{print $19}' /proc/"$p"/stat 2>/dev/null)
  cur_oa=$(cat /proc/"$p"/oom_score_adj 2>/dev/null)
  if [ "$MODE" = "--dry-run" ]; then
    printf '  would set pid=%-8s nice %s->%s oom_score_adj %s->%s\n' "$p" "${cur_ni:-?}" "$want_nice" "${cur_oa:-?}" "$want_oom"
    n_ok=$((n_ok+1)); continue
  fi
  ok=1
  if [ "$MODE" = "--apply" ]; then
    capture_state "$p" || { n_fail=$((n_fail+1)); continue; }
    renice -n "$want_nice" -p "$p" >/dev/null 2>&1 || ok=0
    echo "$want_oom" > /proc/"$p"/oom_score_adj 2>/dev/null || ok=0
    ionice -c 2 -n 7 -p "$p" >/dev/null 2>&1 || ok=0
  else
    state="$STATE_DIR/$p.state"
    if [ ! -r "$state" ]; then
      printf '  cannot revert pid=%s: no captured state\n' "$p" >&2
      ok=0
    else
      read -r saved_start saved_nice saved_oom saved_class saved_prio < "$state"
      if [ "$(start_time "$p")" != "$saved_start" ]; then
        printf '  cannot revert pid=%s: PID was reused\n' "$p" >&2
        ok=0
      else
        # Same-UID Linux processes can restore oom_score_adj and I/O priority.
        # They cannot decrease a raised nice value without CAP_SYS_NICE.
        echo "$saved_oom" > /proc/"$p"/oom_score_adj 2>/dev/null || ok=0
        if [ "$saved_class" = 3 ]; then
          ionice -c 3 -p "$p" >/dev/null 2>&1 || ok=0
        else
          ionice -c "$saved_class" -n "$saved_prio" -p "$p" >/dev/null 2>&1 || ok=0
        fi
        cur_ni=$(awk '{print $19}' /proc/"$p"/stat 2>/dev/null)
        if [ "$cur_ni" != "$saved_nice" ]; then
          printf '  residual pid=%s nice=%s (prior=%s; CAP_SYS_NICE required)\n' \
            "$p" "$cur_ni" "$saved_nice" >&2
        fi
        [ "$ok" = 1 ] && rm -f "$state"
      fi
    fi
  fi
  if [ "$ok" = 1 ]; then n_ok=$((n_ok+1)); else n_fail=$((n_fail+1)); fi
done

printf 'applied=%s skipped=%s failed=%s\n' "$n_ok" "$n_skip" "$n_fail"
[ "$n_fail" -gt 0 ] && exit 1
exit 0
