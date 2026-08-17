#!/bin/bash
# QA BEAAA-23965 — independent confirmation of acceptance criterion 3.
# Runs the CANDIDATE as a real subprocess (as systemd would), drives it with
# curl, and counts upstream POSTs from a log written by a SEPARATE process.
# Nothing here imports the battery or the bridge.
set -u
D=/tmp/qa23965
CAND=/mnt/paperclipdata/dot-paperclip/instances/default/workspaces/88e68019-1cb6-4c4f-83e1-9fd340fb6821/artifacts/beaaa-23961/bridge.py
T=qatoken0123456789
BP=8290; UP=8291
export QA_POSTLOG=$D/posts.log
: > "$QA_POSTLOG"; : > "$D/audit.log"

python3 "$D/stub_upstream.py" $UP & STUB=$!
BRIDGE_TOKEN=$T BRIDGE_PORT=$BP BRIDGE_UPSTREAM=http://127.0.0.1:$UP \
  BRIDGE_AUDIT_LOG=$D/audit.log python3 "$CAND" 2>$D/bridge.stderr & BR=$!
trap 'kill $STUB $BR 2>/dev/null' EXIT
sleep 1.2
B="http://127.0.0.1:$BP/$T"
echo "ping: $(curl -s $B/ping)"
np() { wc -l < "$QA_POSTLOG" | tr -d ' '; }

# payload -> unpadded urlsafe b64, split into 40-char slices
PAY='{"title":"QA-23965 independent probe","description":"'$(printf 'z%.0s' $(seq 1 300))'"}'
B64=$(printf '%s' "$PAY" | base64 -w0 | tr '+/' '-_' | tr -d '=')
SHA12=$(printf '%s' "$PAY" | sha256sum | cut -c1-12)
mapfile -t SL < <(printf '%s' "$B64" | fold -w40)
echo "payload ${#PAY} bytes, sha12=$SHA12, ${#SL[@]} slices"

echo
echo "### P1  gap in slice numbers must not reach upstream"
SID=$(curl -s $B/wnew); N0=$(np)
for i in "${!SL[@]}"; do
  [ "$i" -eq 2 ] && continue            # deliberately omit seq 2
  curl -s -o /dev/null "$B/wput/$SID/$i/${SL[$i]}"
done
echo "  wstat: $(curl -s $B/wstat/$SID)"
echo "  wgo  : $(curl -s -w ' [HTTP %{http_code}]' $B/wgo/$SID/api/companies/X/issues)"
echo "  upstream POSTs added: $(( $(np) - N0 ))   (must be 0)"
echo "  now fill the gap and commit honestly:"
curl -s -o /dev/null "$B/wput/$SID/2/${SL[2]}"
N1=$(np)
echo "  wgo  : $(curl -s -w ' [HTTP %{http_code}]' $B/wgo/$SID/api/companies/X/issues)"
echo "  upstream POSTs added: $(( $(np) - N1 ))   (must be 1)"
echo "  upstream body sha12: $(tail -1 $QA_POSTLOG | awk '{print $3}')  expect $SHA12"

echo
echo "### P2  second commit on a committed session must not reach upstream"
N2=$(np)
echo "  replay wgo (same path)  : $(curl -s -w ' [HTTP %{http_code}]' $B/wgo/$SID/api/companies/X/issues | head -c 200)"
echo "  replay wgo (other path) : $(curl -s -w ' [HTTP %{http_code}]' -o /dev/null $B/wgo/$SID/api/companies/Y/issues)"
echo "  replay declared form    : $(curl -s -w ' [HTTP %{http_code}]' -o /dev/null $B/wgo/$SID/${#SL[@]}/$SHA12/api/companies/X/issues)"
echo "  wput onto committed sid : $(curl -s -w ' [HTTP %{http_code}]' $B/wput/$SID/0/AAAA | head -c 90)"
echo "  wshow (no re-POST)      : $(curl -s -w ' [HTTP %{http_code}]' $B/wshow/$SID)"
echo "  upstream POSTs added: $(( $(np) - N2 ))   (must be 0)"

echo
echo "### P3  concurrent commits on one session must reach upstream exactly once"
SID2=$(curl -s $B/wnew)
for i in "${!SL[@]}"; do curl -s -o /dev/null "$B/wput/$SID2/$i/${SL[$i]}"; done
N3=$(np); : > $D/race.codes; RP=()
for r in $(seq 1 12); do
  ( curl -s -o /dev/null -w '%{http_code}\n' "$B/wgo/$SID2/api/companies/X/issues" >> $D/race.codes ) &
  RP+=($!)
done
wait "${RP[@]}"
echo "  12 concurrent wgo -> codes: $(sort $D/race.codes | uniq -c | tr '\n' ' ')"
echo "  upstream POSTs added: $(( $(np) - N3 ))   (must be 1)"

echo
echo "### P4  wput reply shape (acceptance 4)"
SID3=$(curl -s $B/wnew)
R1=$(curl -s "$B/wput/$SID3/0/AAAA"); R2=$(curl -s "$B/wput/$SID3/1/BBBB")
echo "  reply 1: '$R1'"
echo "  reply 2: '$R2'"
echo "  legacy parser 'ok <n>' -> awk \$1=$(echo "$R2" | awk '{print $1}') \$2=$(echo "$R2" | awk '{print $2}')"
echo "  v5 shape was 'ok <n>' exactly; v6 appends fields after it"

echo
echo "### P5  a gap that is not at the start (seq 0,1 then 9)"
SID4=$(curl -s $B/wnew); N4=$(np)
curl -s -o /dev/null "$B/wput/$SID4/0/AAAA"; curl -s -o /dev/null "$B/wput/$SID4/1/BBBB"
curl -s -o /dev/null "$B/wput/$SID4/9/CCCC"
echo "  wgo: $(curl -s -w ' [HTTP %{http_code}]' $B/wgo/$SID4/api/companies/X/issues)"
echo "  upstream POSTs added: $(( $(np) - N4 ))   (must be 0)"

echo
echo "### P6  1-based slices (client that starts at seq 1) still work"
SID5=$(curl -s $B/wnew); N5=$(np)
for i in "${!SL[@]}"; do curl -s -o /dev/null "$B/wput/$SID5/$((i+1))/${SL[$i]}"; done
echo "  wgo: $(curl -s -w ' [HTTP %{http_code}]' -o /dev/null $B/wgo/$SID5/api/companies/X/issues)"
echo "  upstream POSTs added: $(( $(np) - N5 ))   (must be 1)"
echo "  body sha12: $(tail -1 $QA_POSTLOG | awk '{print $3}')  expect $SHA12"

echo
echo "### P7  declared-count / declared-digest guard"
SID6=$(curl -s $B/wnew); N6=$(np)
for i in "${!SL[@]}"; do curl -s -o /dev/null "$B/wput/$SID6/$i/${SL[$i]}"; done
echo "  wrong n   : $(curl -s -w ' [HTTP %{http_code}]' -o /dev/null $B/wgo/$SID6/99/$SHA12/api/companies/X/issues)"
echo "  wrong sha : $(curl -s -w ' [HTTP %{http_code}]' -o /dev/null $B/wgo/$SID6/${#SL[@]}/deadbeefcafe/api/companies/X/issues)"
echo "  upstream POSTs added so far: $(( $(np) - N6 ))   (must be 0)"
echo "  honest    : $(curl -s -w ' [HTTP %{http_code}]' -o /dev/null $B/wgo/$SID6/${#SL[@]}/$SHA12/api/companies/X/issues)"
echo "  upstream POSTs added: $(( $(np) - N6 ))   (must be 1)"

echo
echo "### P8  empty session / unknown sid"
SID7=$(curl -s $B/wnew); N7=$(np)
echo "  empty wgo : $(curl -s -w ' [HTTP %{http_code}]' $B/wgo/$SID7/api/companies/X/issues)"
echo "  bogus sid : $(curl -s -w ' [HTTP %{http_code}]' $B/wgo/deadsid/api/companies/X/issues)"
echo "  upstream POSTs added: $(( $(np) - N7 ))   (must be 0)"

echo
echo "### total upstream POSTs this run: $(np)"
cat "$QA_POSTLOG"
