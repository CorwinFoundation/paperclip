#!/usr/bin/env bash
# BEAAA-33250 — the verification the CEO named as what closes gate 3's latency criterion.
# Run AFTER retune-hook-emit.sh. Read-only on the spools. Exit 0 = gate-3 evidence is good.
set -uo pipefail
MANAGED_DIR=/usr/local/libexec/numbat-paperclip
RECORDS=/mnt/paperclipdata/numbat/records
IDENTITIES=(cto auditor scanner-engineer-2)
fail=0

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
echo "   (counts records written strictly after the retune timestamp passed as \$1, ISO8601)"
since="${1:-}"
for id in "${IDENTITIES[@]}"; do
  python3 - "$RECORDS/$id" "$since" <<'PY'
import gzip,json,glob,os,sys,collections
d,since=sys.argv[1],sys.argv[2]
files=sorted(glob.glob(d+"/records.*.ndjson.gz"))[-3:]
live=os.path.join(d,"records.ndjson")
if os.path.exists(live): files.append(live)
types=collections.Counter(); n=0; last=None
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
        if since and ts <= since: continue
        n+=1; types[r.get('record_type','?')]+=1; last=ts or last
print("  %-19s post-retune records=%-6d last=%s types=%s %s" %
      (os.path.basename(d), n, last, dict(types), "OK" if n>0 else "NO NEW EVENTS"))
PY
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
[ $? -ne 0 ] && fail=1

echo
[ $fail -eq 0 ] && echo "RESULT: gate-3 latency evidence COMPLETE" || echo "RESULT: FAILED — do not grade gate 3"
exit $fail
