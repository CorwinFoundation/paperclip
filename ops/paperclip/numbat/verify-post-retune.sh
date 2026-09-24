#!/usr/bin/env bash
# BEAAA-33250 — the verification the CEO named as what closes gate 3's latency criterion.
# Run AFTER retune-hook-emit.sh. Read-only on the spools.
#
# Exit 0 = the four evidence preconditions hold. It does NOT measure latency:
# the p50/p90 numbers come from the BEAAA-33227 two-leg method and are graded
# against the band pre-registered there. This script proves the retune landed
# and that nothing the isolation and continuous-capture checks read was dropped.
#
#   verify-post-retune.sh <retune-timestamp-ISO8601> [max-staleness-minutes]
#
# The timestamp is REQUIRED. Without it "records written after the retune" is
# indistinguishable from "every record ever retained", which is how an earlier
# revision reported a healthy spool no matter what the spool contained.
#
# Continuity is graded on SPOOL AGE, not on the post-retune count. The cto seat
# is fed by an hourly cron (BEAAA-24636 numbat-claude-cohort-scan.py, :00), so a
# zero count is normal for up to an hour after the retune and is NOT a failure.
# A spool whose newest record is older than its budget is a real capture outage
# and fails closed. max-staleness-minutes is the FLOOR of that budget, not the
# budget: 180 is the cto seat's number — cron period 60m plus a measured ~64m
# intra-scan lag (2026-09-17, the 00:00 run's newest record was 22:56Z) plus
# slack. Applied uniformly to three seats with different activity rates it reads
# an idle or errored seat as a capture outage, which is the failure BEAAA-33250
# hit on 2026-09-18: scanner-engineer-2 at 234m, idle, graded STALE against the
# cto seat's cadence. Each seat may therefore widen the floor to twice its own
# p90 inter-session gap, capped at 24h, and the widening is printed on the line.
# Each seat's Paperclip agent status is printed beside its spool age so a quiet
# seat is visibly distinguishable from a broken one; status never grades.
# Widening requires a real sample: under 5 observed inter-session gaps the floor
# stands, because a p90 over a single gap widens hardest where the evidence is
# thinnest and would hand the busiest seat the 24h cap.
set -uo pipefail
MANAGED_DIR=/usr/local/libexec/numbat-paperclip
RECORDS=/mnt/paperclipdata/numbat/records
IDENTITIES=(cto auditor scanner-engineer-2)
fail=0

since="${1:-}"
max_stale_min="${2:-180}"
if [ -z "$since" ]; then
  echo "usage: $(basename "$0") <retune-timestamp-ISO8601> [max-staleness-minutes]" >&2
  echo "  e.g. $(basename "$0") 2026-09-17T08:00:00Z 180" >&2
  exit 2
fi

echo "== 1. re-pinned SHA-256 (four pinned artifacts) =="
sha256sum /etc/codex/requirements.toml "$MANAGED_DIR"/cto "$MANAGED_DIR"/auditor "$MANAGED_DIR"/scanner-engineer-2

echo
echo "== 2. retune actually applied to all three wrappers =="
for id in "${IDENTITIES[@]}"; do
  e=$(grep -o -- '--emit [a-z]*' "$MANAGED_DIR/$id" 2>/dev/null)
  printf '  %-19s %s\n' "$id" "${e:-MISSING}"
  [ "$e" = "--emit events" ] || fail=1
done

echo
echo "== 3. continuous capture: events still landing for ALL THREE seats =="
echo "   (post-retune count is informational. Staleness is graded PER SEAT against"
echo "    that seat's own observed capture cadence, floored at ${max_stale_min}m and"
echo "    capped at 1440m. A widened budget is printed, never applied silently.)"

# Agent status is context, not a grade input: it is what lets a reader tell
# "this seat produced nothing because it is idle or errored" from "capture for
# this seat is broken". Best-effort — the script still grades without it.
declare -A AGENT_STATUS
_agent_status_lines=$(python3 - <<'PY'
import json,os,urllib.request
ids={"88e68019":"cto","0c4b497c":"auditor","bc8396d5":"scanner-engineer-2"}
base=os.environ.get("PAPERCLIP_API_URL"); key=os.environ.get("PAPERCLIP_API_KEY")
cid=os.environ.get("PAPERCLIP_COMPANY_ID")
if base and key and cid:
    try:
        req=urllib.request.Request("%s/api/companies/%s/agents"%(base.rstrip("/"),cid),
                                   headers={"Authorization":"Bearer "+key})
        d=json.load(urllib.request.urlopen(req,timeout=10))
        rows=d if isinstance(d,list) else d.get("agents",d.get("data",[]))
        for r in rows:
            pfx=str(r.get("id",""))[:8]
            if pfx in ids:
                print("%s=%s"%(ids[pfx], r.get("status") or "unknown"))
    except Exception:
        pass
PY
)
while IFS='=' read -r _k _v; do
  [ -n "$_k" ] && AGENT_STATUS["$_k"]="$_v"
done <<< "$_agent_status_lines"

for id in "${IDENTITIES[@]}"; do
  python3 - "$RECORDS/$id" "$since" "$max_stale_min" "${AGENT_STATUS[$id]:-unknown}" <<'PY'
import gzip,json,glob,os,re,sys,collections,datetime
d,since,floor_min,status=sys.argv[1],sys.argv[2],float(sys.argv[3]),sys.argv[4]

# A seat's spool age only means "capture is broken" relative to how often THAT
# seat produces. The 180m default is calibrated on the cto seat's hourly cron
# (BEAAA-33250, 2026-09-18): applied uniformly it reads an idle or errored seat
# as a capture outage. So the floor stays, and each seat may widen it to twice
# its own p90 inter-session gap, capped at 24h so the check cannot go vacuous.
SESSION_GAP_MIN=5.0
BUDGET_CAP_MIN=1440.0
# A p90 over one or two observed gaps is not a cadence, it is an anecdote, and it
# widens hardest exactly where the sample is thinnest. Measured 2026-09-24 on the
# busiest seat: cto saw a single gap of 1592m across its retained window and was
# handed the full 1440m cap, i.e. the check went vacuous for the seat whose
# capture matters most. Below this many gaps the floor stands unwidened.
MIN_GAPS_FOR_CADENCE=5
# Cadence is sampled over a WIDER window than the count. The count only needs the
# newest few rotations; a cadence needs enough sessions to be a distribution. With
# the count's 3-file window every seat showed 1-3 gaps on 2026-09-24 and the floor
# held for all of them, i.e. the per-seat budget never engaged and the original
# uniform-180m defect was still live. Spools retain ~7 days here.
CADENCE_FILES=120

def parse(ts):
    """ISO8601 with 0-9 fractional digits and a Z suffix."""
    t=re.sub(r"(\.\d{6})\d+",r"\1",ts.strip()).replace("Z","+00:00")
    try: return datetime.datetime.fromisoformat(t)
    except ValueError: return None

files=sorted(glob.glob(d+"/records.*.ndjson.gz"))[-3:]
live=os.path.join(d,"records.ndjson")
if os.path.exists(live): files.append(live)
types=collections.Counter(); n=0; newest=None; undated=0; dated=[]
for f in files:
    op=gzip.open if f.endswith(".gz") else open
    try: fh=op(f,'rt',errors='replace')
    except Exception: continue
    for l in fh:
        l=l.strip()
        if not l: continue
        try: r=json.loads(l)
        except Exception: continue
        ts=r.get('timestamp') or r.get('ts') or ''
        # A record with no timestamp cannot be placed relative to the retune.
        # Count it out loud rather than dropping it into the <= comparison,
        # where '' sorts before every real timestamp and disappears silently.
        if not ts:
            undated += 1
            continue
        if newest is None or ts > newest: newest = ts
        if ts <= since: continue
        n+=1; types[r.get('record_type','?')]+=1

# Cadence sample: timestamps only, over a longer file window than the count.
for f in sorted(glob.glob(d+"/records.*.ndjson.gz"))[-CADENCE_FILES:]+([live] if os.path.exists(live) else []):
    op=gzip.open if f.endswith(".gz") else open
    try: fh=op(f,'rt',errors='replace')
    except Exception: continue
    with fh:
        for line in fh:
            line=line.strip()
            if not line: continue
            try: r=json.loads(line)
            except Exception: continue
            dt=parse(r.get('timestamp') or r.get('ts') or '')
            if dt is not None: dated.append(dt)

def pct(v,p):
    if not v: return None
    s=sorted(v); k=min(len(s)-1,int(round((p/100.0)*(len(s)-1))))
    return s[k]

# Cadence is the gap between capture SESSIONS, not between records: one scan
# writes a burst of records seconds apart, so per-record gaps measure burst
# density and not how often the seat is captured at all.
dated.sort()
starts=[dated[0]] if dated else []
for a,b in zip(dated,dated[1:]):
    if (b-a).total_seconds()/60.0 > SESSION_GAP_MIN: starts.append(b)
gaps=[(b-a).total_seconds()/60.0 for a,b in zip(starts,starts[1:])][-200:]
p50=pct(gaps,50); p90=pct(gaps,90)

budget=floor_min; widened=""
if len(gaps) < MIN_GAPS_FOR_CADENCE:
    widened=" [floor held: %d gap(s) observed, need %d to infer a cadence]"%(len(gaps),MIN_GAPS_FOR_CADENCE)
elif p90 is not None and 2.0*p90 > floor_min:
    budget=min(2.0*p90,BUDGET_CAP_MIN)
    widened=" [widened from %.0fm floor by own cadence p90=%.0fm over %d gaps]"%(floor_min,p90,len(gaps))

age_min=None
dt=parse(newest) if newest else None
if dt is not None:
    age_min=(datetime.datetime.now(datetime.timezone.utc)-dt).total_seconds()/60.0

if age_min is None:
    verdict="NO DATED RECORDS"; ok=False
elif age_min > budget:
    verdict="STALE %.0fm > budget %.0fm"%(age_min,budget); ok=False
else:
    verdict="OK (spool %.0fm old, budget %.0fm)"%(age_min,budget); ok=True

print("  %-19s agent=%-8s post-retune=%-6d newest=%s undated=%-4d cadence p50/p90=%s/%s min types=%s %s%s" %
      (os.path.basename(d), status, n, newest, undated,
       "n/a" if p50 is None else "%.0f"%p50, "n/a" if p90 is None else "%.0f"%p90,
       dict(types), verdict, widened))
raise SystemExit(0 if ok else 1)
PY
  [ $? -eq 0 ] || fail=1
done

echo "== 4. isolation: no cross-identity id in identity-bearing fields =="
python3 - "$RECORDS" <<'PY'
import gzip,json,glob,os,sys
R=sys.argv[1]
ids={"cto":"88e68019","auditor":"0c4b497c","scanner-engineer-2":"bc8396d5"}
bad=0
for ident in ids:
    files=sorted(glob.glob(R+"/%s/records.*.ndjson.gz"%ident))[-3:]
    lv=R+"/%s/records.ndjson"%ident
    if os.path.exists(lv): files.append(lv)
    hits=0
    for f in files:
        op=gzip.open if f.endswith(".gz") else open
        try: fh=op(f,'rt',errors='replace')
        except Exception: continue
        for l in fh:
            l=l.strip()
            if not l: continue
            try: r=json.loads(l)
            except Exception: continue
            idf=" ".join(str(r.get(k,"")) for k in ("actor","run_id","session_id","project_path","endpoint","project_path_hash"))
            for o,uu in ids.items():
                if o!=ident and uu in idf: hits+=1
    print("  %-19s cross-identity in id fields: %d %s" % (ident,hits,"OK" if hits==0 else "CONTAMINATED"))
    bad += hits
raise SystemExit(1 if bad else 0)
PY
[ $? -eq 0 ] || fail=1

echo
if [ $fail -eq 0 ]; then
  echo "RESULT: retune applied, capture continuous, isolation clean."
  echo "        Latency is NOT measured here — grade p50/p90 from the BEAAA-33227 two-leg method."
else
  echo "RESULT: FAILED — do not grade gate 3"
fi
exit $fail
