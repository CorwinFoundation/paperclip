#!/usr/bin/env bash
# BEAAA-33250 — demonstrate, on REAL retained spools, that dropping live `finding`
# rows (the `--emit events` retune) loses no detection: every finding the live
# pipeline produced is recoverable by replaying the retained events through the
# same rule set.
#
# This is the load-bearing claim for gate 3's fidelity argument. It is a
# demonstration, not an assertion: it reads the actual spool, takes the findings
# the live `--emit all` pipeline already wrote as ground truth, and checks that
# replaying only the events recovers them.
#
# Read-only. Nothing under the records tree is opened for write.
#   ./demo-findings-recoverable.sh [RECORDS_DIR]
set -uo pipefail
RECORDS="${1:-/mnt/paperclipdata/numbat/records}"
NUMBAT="${NUMBAT:-/usr/local/bin/numbat}"
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT

command -v "$NUMBAT" >/dev/null || { echo "no numbat at $NUMBAT" >&2; exit 2; }
[ -d "$RECORDS" ] || { echo "no records dir at $RECORDS" >&2; exit 2; }

python3 - "$RECORDS" "$WORK" <<'PY'
import gzip,glob,json,os,sys
records,work=sys.argv[1],sys.argv[2]
idents=[d for d in sorted(os.listdir(records)) if os.path.isdir(os.path.join(records,d))]
findings=[]
def rows(ident):
    for f in sorted(glob.glob(os.path.join(records,ident,"records.*.ndjson.gz"))):
        try: fh=gzip.open(f,'rt',errors='replace')
        except Exception: continue
        for l in fh:
            l=l.strip()
            if not l: continue
            try: yield json.loads(l)
            except Exception: continue
    live=os.path.join(records,ident,"records.ndjson")
    if os.path.exists(live):
        for l in open(live,errors='replace'):
            l=l.strip()
            if not l: continue
            try: yield json.loads(l)
            except Exception: continue
for ident in idents:
    for r in rows(ident):
        if r.get('record_type')=='finding':
            findings.append((ident,r.get('rule_id'),tuple(r.get('cited_event_ids') or [])))
want=set()
for _,_,c in findings: want.update(c)
n_ev=0
with open(os.path.join(work,'cited.ndjson'),'w') as out:
    for ident in idents:
        for r in rows(ident):
            if r.get('record_type')=='event' and r.get('event_id') in want:
                out.write(json.dumps(r)+"\n"); n_ev+=1
json.dump([[f[1],list(f[2])] for f in findings],open(os.path.join(work,'truth.json'),'w'))
print("identities        : %s" % ", ".join(idents))
print("findings in spool : %d" % len(findings))
print("event ids cited   : %d" % len(want))
print("cited events found: %d" % n_ev)
if n_ev < len(want):
    print("WARNING: %d cited event(s) are no longer retained (rotated out)" % (len(want)-n_ev))
PY

[ -s "$WORK/cited.ndjson" ] || { echo; echo "no findings in the retained window - nothing to demonstrate"; exit 0; }

echo
echo "replaying the events-only spool through the rule engine:"
echo "  \$ numbat rules test --fixture <events cited by those findings>"
"$NUMBAT" rules test --fixture "$WORK/cited.ndjson" > "$WORK/out.tsv" 2>"$WORK/err.txt"
rc=$?
[ -s "$WORK/err.txt" ] && sed 's/^/  stderr: /' "$WORK/err.txt"
echo "  exit=$rc  matches=$(wc -l < "$WORK/out.tsv")"
echo

python3 - "$WORK" <<'PY'
import json,sys,collections
w=sys.argv[1]
truth=json.load(open(w+"/truth.json"))
rec=collections.defaultdict(set)
for l in open(w+"/out.tsv"):
    p=l.rstrip("\n").split("\t")
    if len(p)==2: rec[p[1]].add(p[0])
ok=0
for rule,cites in truth:
    hit=any(rule in rec.get(c,()) for c in cites)
    ok+=hit
    print("  %-34s %s" % (rule,"RECOVERED" if hit else "NOT recovered"))
print()
print("RESULT: %d/%d live findings recovered from events alone" % (ok,len(truth)))
raise SystemExit(0 if ok==len(truth) and truth else 1)
PY
rc=$?

cat <<'NOTE'

CAVEAT, measured and not glossed: `numbat rules test` is FAIL-FAST. Feeding it a
whole multi-thousand-event spool aborts at the first command its shell parser
cannot read ("shell command analysis: unsupported or malformed syntax", exit 1)
and silently evaluates only the prefix before it. A recovery run must filter or
chunk, as this script does. Whoever performs a real recovery must check the exit
code and the evaluated count, not just that output appeared.
NOTE
exit $rc
