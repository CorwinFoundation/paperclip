#!/bin/bash
# QA BEAAA-23965 — mutation check. Each mutant disables ONE v6 guard. If the
# battery still scores 12/12 on a mutant, that guard is not actually proven.
set -u
A=/mnt/paperclipdata/dot-paperclip/instances/default/workspaces/88e68019-1cb6-4c4f-83e1-9fd340fb6821/artifacts/beaaa-23961
M=/tmp/qa23965/mut; mkdir -p $M

mk() { cp "$A/bridge.py" "$M/$1.py"; }

# M1 wgo replay guard: never see a committed session
mk M1; python3 - "$M/M1.py" <<'EOF'
import sys; p=sys.argv[1]; s=open(p).read()
s=s.replace('                if s["committed"]:\n                    c = s["committed"]','                if False:\n                    c = s["committed"]',1)
open(p,"w").write(s)
EOF
# M2 in_flight guard
mk M2; sed -i 's/^                if s\["in_flight"\]:$/                if False:/' "$M/M2.py"
# M3 contiguity check
mk M3; sed -i 's/^    if keys != expect:$/    if False:/' "$M/M3.py"
# M4 empty-session check
mk M4; sed -i 's/^    if not keys:$/    if False:/' "$M/M4.py"
# M5 wput dup-seq guard
mk M5; sed -i 's/^                if prev is not None and prev != chunk:$/                if False:/' "$M/M5.py"
# M6 short sid (v5 entropy)
mk M6; sed -i 's/secrets.token_urlsafe(WSID_BYTES)/secrets.token_hex(3)/' "$M/M6.py"
# M7 wnew collision guard: take the candidate even if live
mk M7; sed -i 's/^                    if cand not in WSESS:      # never clobber a live session$/                    if True:/' "$M/M7.py"
# M8 no _wgc from wput (expiry only via wnew, as v5)
mk M8; python3 - "$M/M8.py" <<'EOF'
import sys; p=sys.argv[1]; s=open(p).read()
s=s.replace('        if verb == "wput":\n            _wgc()\n','        if verb == "wput":\n',1)
open(p,"w").write(s)
EOF
# M9 declared count/digest guards
mk M9; sed -i 's/^                elif want_n is not None and want_n != n:$/                elif False:/;s/^                elif want_sha is not None and want_sha != got_sha:$/                elif False:/' "$M/M9.py"

cd "$A"
for m in M1 M2 M3 M4 M5 M6 M7 M8 M9; do
  if cmp -s "$M/$m.py" "$A/bridge.py"; then echo "$m  !! NO-OP MUTANT (sed did not apply)"; continue; fi
  out=$(timeout 180 python3 test_bridge_wsess.py "$M/$m.py" 2>&1)
  score=$(echo "$out" | grep -oE '^[0-9]+/12 passed' | head -1)
  failed=$(echo "$out" | sed -n 's/.*FAILED: //p' | head -1)
  echo "$m  $score  killed-by: ${failed:-<NONE - MUTANT SURVIVED>}"
done
