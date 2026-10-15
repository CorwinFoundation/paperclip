#!/bin/sh
# BEAAA-41114: re-judge the VAPI judge-402 rows sequentially within the remaining aggregate USD 7 ceiling,
# then post the result on the issue (that comment is the wake). The reference OSS row (1) was
# re-judged in the foreground smoke at 19:27Z for USD 0.0046.
set -u
cd "$1"
R=artifacts/BEAAA-41114/full-run
LOG=$R/rejudge_driver.log
LEFT=6.99
RC=0
for f in $R/vapi_09aea361/oss/oss_conv_live_vapi_09aea361_rows.jsonl $R/vapi_09aea361/conv-en/conv_live_vapi_09aea361_rows.jsonl; do
  echo "== $(date -u +%FT%TZ) start $f cap=$LEFT" >> $LOG
  OUT=$(python3 scripts/beaaa41114_rejudge_402.py --rows "$f" --cap-usd "$LEFT" --workers 12 2>>$LOG)
  RC=$?
  echo "$OUT" >> $LOG
  echo "== $(date -u +%FT%TZ) done rc=$RC $f" >> $LOG
  SPENT=$(printf '%s' "$OUT" | python3 -c "import json,sys;print(json.load(sys.stdin)['spend_usd'])" 2>/dev/null || echo 0)
  LEFT=$(python3 -c "print(round($LEFT-$SPENT,4))")
  [ "$RC" = 0 ] || break
done
echo "== $(date -u +%FT%TZ) FINISHED rc=$RC left=$LEFT" >> $LOG
python3 - "$LOG" "$RC" <<'PY'
import json,os,sys,urllib.request
log=open(sys.argv[1]).read()[-3000:]
body={"body":f"Re-judge driver (judge HTTP 402 rows, VAPI OSS then conv-en) **exited rc={sys.argv[2]}**. Log tail:\n```\n{log}\n```\nNext: verify still_pending=0, re-gzip rows, regenerate report, attach to BEAAA-41113."}
api=os.environ.get("PAPERCLIP_API_URL","http://127.0.0.1:3100")
for hdr in ({"Authorization":"Bearer "+os.environ.get("PAPERCLIP_API_KEY","")},{}):
    try:
        urllib.request.urlopen(urllib.request.Request(api+"/api/issues/BEAAA-41114/comments",data=json.dumps(body).encode(),method="POST",headers={"Content-Type":"application/json",**hdr}))
        print("comment posted"); break
    except Exception as e: print("comment failed",e)
PY
