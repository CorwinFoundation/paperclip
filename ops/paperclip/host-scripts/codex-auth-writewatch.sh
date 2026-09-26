#!/bin/sh
# BEAAA-35987: record the SHAPE (never the value) of every write to the one
# shared Codex credential all 20 codex_local seats symlink to. Idempotent.
F=/home/beai-agent/.codex/auth.json
L=/home/beai-agent/.codex/auth-writewatch.log
while inotifywait -qq -e close_write,moved_to "$F" 2>/dev/null; do
  /usr/bin/python3 - "$F" >> "$L" 2>&1 <<'PY'
import json,sys,datetime
try: d=json.load(open(sys.argv[1]))
except Exception as e: d={"_unparseable":str(e)}
k=d.get("OPENAI_API_KEY")
print(json.dumps({"at":datetime.datetime.now(datetime.timezone.utc).isoformat(),
 "auth_mode":d.get("auth_mode"),"api_key_present":bool(k),
 "api_key_prefix":(k[:11] if isinstance(k,str) else None),
 "has_tokens":bool(d.get("tokens")),"last_refresh":d.get("last_refresh")}))
PY
done
