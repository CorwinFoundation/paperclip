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
# A spool whose newest record is older than max-staleness-minutes is a real
# capture outage and fails closed. The default is 180: the cron period is 60m,
# and the scan itself only picks up settled transcript segments — measured
# 2026-09-17, the 00:00 run's newest record was 22:56Z, an intra-scan lag of
# ~64m. 60 + 64 + slack is why 180 and not 90. A tighter budget produces false
# failures on a seat that is capturing perfectly well.
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
echo "   (post-retune count is informational; a spool older than ${max_stale_min}m FAILS)"
for id in "${IDENTITIES[@]}"; do
  python3 - "$RECORDS/$id" "$since" "$max_stale_min" <<'PY'
import gzip,json,glob,os,re,sys,collections,datetime
d,since,max_stale=sys.argv[1],sys.argv[2],float(sys.argv[3])

def parse(ts):
    """ISO8601 with 0-9 fractional digits and a Z suffix."""
    t=re.sub(r"(\.\d{6})\d+",r"\1",ts.strip()).replace("Z","+00:00")
    try: return datetime.datetime.fromisoformat(t)
    except ValueError: return None

files=sorted(glob.glob(d+"/records.*.ndjson.gz"))[-3:]
live=os.path.join(d,"records.ndjson")
if os.path.exists(live): files.append(live)
types=collections.Counter(); n=0; last=None; newest=None; undated=0
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
        n+=1; types[r.get('record_type','?')]+=1; last=ts

age_min=None
dt=parse(newest) if newest else None
if dt is not None:
    age_min=(datetime.datetime.now(datetime.timezone.utc)-dt).total_seconds()/60.0

if age_min is None:
    verdict="NO DATED RECORDS"; ok=False
elif age_min > max_stale:
    verdict="STALE %.0fm > %.0fm" % (age_min,max_stale); ok=False
else:
    verdict="OK (spool %.0fm old)" % age_min; ok=True

print("  %-19s post-retune=%-6d newest=%s undated=%-4d types=%s %s" %
      (os.path.basename(d), n, newest, undated, dict(types), verdict))
raise SystemExit(0 if ok else 1)
PY
  [ $? -eq 0 ] || fail=1
done

echo
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
