"""Detached BEAAA-41114 driver: wait for conv-en, then run OSS; comment on the issue after each."""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path
ROOT = Path(sys.argv[1]); os.chdir(ROOT)
BASE = "artifacts/BEAAA-41114/full-run/ref_openai_gpt-4.1-mini"
API = "http://localhost:3100/api/issues/BEAAA-41114/comments"

def alive(pid):
    try: os.kill(pid, 0); return True
    except OSError: return False

def status(d, stem):
    rows = [json.loads(l) for l in open(f"{d}/{stem}_rows.jsonl") if l.strip()] if Path(f"{d}/{stem}_rows.jsonl").exists() else []
    spend = sum(float(r.get("target_cost_usd") or 0) + sum(float(j.get("cost_usd") or 0) for j in r.get("judges") or []) for r in rows)
    v = {}
    for r in rows: v[r["verdict"]] = v.get(r["verdict"], 0) + 1
    tail = Path(f"{d}/run.log").read_text()[-600:] if Path(f"{d}/run.log").exists() else ""
    return f"attempts retained {len(rows)}, attempt verdicts {v}, retained spend ${spend:.4f}\n\nlog tail:\n```\n{tail}\n```"

def comment(body):
    req = urllib.request.Request(API, data=json.dumps({"body": body}).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ['PAPERCLIP_API_KEY']}"})
    try: urllib.request.urlopen(req, timeout=30).read()
    except Exception as exc: print("comment failed", exc, flush=True)

pid = int(Path(f"{BASE}/conv-en/run.pid").read_text())
while alive(pid): time.sleep(60)
comment("Driver: conv-en reference run (gpt-4.1-mini, K=3) process exited. " + status(f"{BASE}/conv-en", "conv_live_openai_gpt-4.1-mini") + "\nStarting OSS reference run next (cap $12).")
with open(f"{BASE}/oss/run.log", "w") as log:
    rc = subprocess.call([sys.executable, "scripts/beaaa38922_conv_live_run.py", "--corpus", "oss", "--seeds", "42", "43", "44",
                          "--cap-usd", "12", "--workers", "12", "--out", f"{BASE}/oss"], stdout=log, stderr=subprocess.STDOUT)
comment(f"Driver: OSS reference run exited rc={rc}. " + status(f"{BASE}/oss", "oss_conv_live_openai_gpt-4.1-mini"))
