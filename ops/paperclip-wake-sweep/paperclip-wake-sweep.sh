#!/usr/bin/env bash
# PERMANENT wake sweep (revised 2026-08-28): wakes live agents holding
# actionable `todo` or `in_review` work whose owner is idle, because plain
# todo/in_review do NOT auto-generate a wake (only events do). Runs every cycle
# and NO-OPs cheaply when there's nothing to wake. Does NOT self-terminate — the
# todo-doesn't-self-wake gap is permanent until the app-side fix ships.
#
# Source of truth for /usr/local/bin/paperclip-wake-sweep.sh on AriClaw.
# Install: sudo install -m 0755 ops/paperclip-wake-sweep/paperclip-wake-sweep.sh /usr/local/bin/
set -u
STATE=/var/lib/paperclip-wake-sweep
mkdir -p "$STATE"
LOG="$STATE/sweep.log"
C=59f8876e-e729-4dda-98f9-1317c2b50492
V=paperclipai@2026.626.0
export PGHOST=127.0.0.1 PGPORT=54329 PGUSER=paperclip PGPASSWORD=paperclip PGDATABASE=paperclip
log(){ echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

# live agents that own actionable todo/in_review work and are idle (>4 min idle)
IDS=$(psql -tAc "
select distinct a.id
from issues i join agents a on a.id=i.assignee_agent_id
where i.status in ('todo','in_review')
  and a.status not in ('paused','terminated')
  and (a.last_heartbeat_at is null or a.last_heartbeat_at < now()-interval '4 minutes')
  -- never double-wake an agent with a run already in flight (Codex thread-store
  -- conflict: 'thread already has an active writer'); guards long (>4min) runs
  and not exists (select 1 from heartbeat_runs hr
                  where hr.agent_id=a.id and hr.status in ('running','queued','claimed'))
  -- skip in_review issues already waiting on a pending interaction that will
  -- wake the assignee when answered; sweeping them only adds empty wakes
  -- (BEAAA-46817)
  and not (i.status='in_review' and exists (
    select 1 from issue_thread_interactions x
    where x.issue_id=i.id and x.status='pending'
      and x.continuation_policy='wake_assignee'))")

[ -z "$IDS" ] && { log "no idle actionable owners"; exit 0; }

n=0
for ID in $IDS; do
  TOK=$(sudo -iu beai-agent bash -lc "cd /home/beai-agent && npx -y $V agent local-cli $ID --company-id $C --key-name 'ops-autosweep' --no-install-skills --json 2>/dev/null" | python3 -c 'import sys,json;print(json.load(sys.stdin)["key"]["token"])' 2>/dev/null)
  [ -z "${TOK:-}" ] && { log "$ID mint_fail"; continue; }
  sudo -iu beai-agent bash -lc "cd /home/beai-agent && npx -y $V agent wake $ID --company-id $C --api-key $TOK --json >/dev/null 2>&1"
  n=$((n+1))
done
psql -tAc "update agent_api_keys set revoked_at=now() where name='ops-autosweep' and revoked_at is null" >/dev/null
log "woke $n idle actionable owners"
