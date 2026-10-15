"""BEAAA-41114 sequential driver v2 (replaces driver.py + h_watcher.py).
Waits for conv-en AND the 813-case H run to exit, then runs OSS, then Full Scan U.
One heavy run at a time; each phase posts an authenticated comment with retained counts and spend.
H spend is summed from retained raw_responses[].usage.cost (H rows carry no target_cost_usd)."""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path
ROOT = Path(sys.argv[1]); os.chdir(ROOT)
BASE = "artifacts/BEAAA-41114/full-run/ref_openai_gpt-4.1-mini"
API = "http://localhost:3100/api/issues/BEAAA-41114/comments"

def alive(pid):
    try: os.kill(pid, 0); return True
    except OSError: return False

def rows(p):
    p = Path(p)
    out = []
    if p.exists():
        for l in p.open():
            try: out.append(json.loads(l))
            except Exception: pass
    return out

def tail(d):
    p = Path(f"{d}/run.log"); return p.read_text()[-600:] if p.exists() else ""

def conv_status(d, stem):
    rs = rows(f"{d}/{stem}_rows.jsonl")
    spend = sum(float(r.get("target_cost_usd") or 0) + sum(float(j.get("cost_usd") or 0) for j in r.get("judges") or []) for r in rs)
    v = {}
    for r in rs: v[r.get("verdict")] = v.get(r.get("verdict"), 0) + 1
    return f"attempts retained {len(rs)}, attempt verdicts {v}, retained spend ${spend:.4f}\n\nlog tail:\n```\n{tail(d)}\n```"

def h_status():
    d = f"{BASE}/full_scan_h"; rs = rows(f"{d}/full_scan_h_live_openai_gpt-4.1-mini_rows.jsonl")
    spend = sum(float((x.get("usage") or {}).get("cost") or 0) for r in rs for x in (r.get("raw_responses") or []) if isinstance(x, dict))
    dl = sum(1 for r in rs if r.get("injection_delivered"))
    v = {}
    for r in rs:
        k = (r.get("verdict") if r.get("injection_delivered") else "could_not_run/injection_not_delivered")
        v[k] = v.get(k, 0) + 1
    return (f"attempts retained {len(rs)} (expected 813x5=4065), injection_delivered={dl}, normalized {v}, "
            f"spend from retained usage.cost ${spend:.4f}\n\nlog tail:\n```\n{tail(d)}\n```")

def u_status():
    d = f"{BASE}/full_scan_u"; rs = rows(f"{d}/full_scan_u_live_openai_gpt-4.1-mini_rows.jsonl")
    v = {}
    for r in rs: v[r.get("verdict")] = v.get(r.get("verdict"), 0) + 1
    return f"attempts retained {len(rs)} (expected 1300), verdicts {v}\n\nlog tail:\n```\n{tail(d)}\n```"

def comment(body):
    req = urllib.request.Request(API, data=json.dumps({"body": body}).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ['PAPERCLIP_API_KEY']}"})
    try: urllib.request.urlopen(req, timeout=30).read(); print("comment posted", flush=True)
    except Exception as exc: print("comment failed", exc, flush=True)

def run(cmd, out):
    Path(out).mkdir(parents=True, exist_ok=True)
    with open(f"{out}/run.log", "a") as log:
        p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONPATH": "."})
        Path(f"{out}/run.pid").write_text(str(p.pid)); return p.wait()

conv_pid = int(Path(f"{BASE}/conv-en/run.pid").read_text()); h_pid = int(Path(f"{BASE}/full_scan_h/run.pid").read_text())
posted = set()
while alive(conv_pid) or alive(h_pid):
    if not alive(h_pid) and "h" not in posted:
        comment("Driver v2: **Full Scan H 813-case run (K=5) exited.** " + h_status()); posted.add("h")
    if not alive(conv_pid) and "c" not in posted:
        comment("Driver v2: **conv-en reference run (gpt-4.1-mini, K=3) exited.** " + conv_status(f"{BASE}/conv-en", "conv_live_openai_gpt-4.1-mini")); posted.add("c")
    time.sleep(30)
if "h" not in posted: comment("Driver v2: **Full Scan H 813-case run (K=5) exited.** " + h_status())
if "c" not in posted: comment("Driver v2: **conv-en reference run (gpt-4.1-mini, K=3) exited.** " + conv_status(f"{BASE}/conv-en", "conv_live_openai_gpt-4.1-mini"))
comment("Driver v2: starting OSS reference run (1,734 cases, K=3, cap $12); U follows after OSS exits.")
rc = run([sys.executable, "scripts/beaaa38922_conv_live_run.py", "--corpus", "oss", "--seeds", "42", "43", "44",
          "--cap-usd", "12", "--workers", "12", "--out", f"{BASE}/oss"], f"{BASE}/oss")
comment(f"Driver v2: **OSS reference run exited rc={rc}.** " + conv_status(f"{BASE}/oss", "oss_conv_live_openai_gpt-4.1-mini") + "\n\nStarting Full Scan U (260 cases x K=5, cap $4).")
rc = run([sys.executable, "scripts/beaaa41114_full_scan_u_live_run.py", "--k", "5", "--cap-usd", "4", "--workers", "8",
          "--resume", "--out", f"{BASE}/full_scan_u"], f"{BASE}/full_scan_u")
comment(f"Driver v2: **Full Scan U exited rc={rc}.** " + u_status() + "\n\nAll reference-target corpus runs complete; next: VAPI target runs + report generation.")
