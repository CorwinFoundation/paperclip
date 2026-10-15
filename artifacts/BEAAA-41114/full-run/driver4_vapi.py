"""BEAAA-41114 sequential driver v4 (replaces v3 mid-run): VAPI target (BEAAA-39144 assistant), conv-en then OSS, K=3.
One heavy run at a time; each phase posts a comment with retained counts, OpenRouter judge spend
and VAPI spend. Both runs use --resume semantics of the conv runner (skips retained (case, seed))."""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path
ROOT = Path(sys.argv[1]); os.chdir(ROOT)
AID = "09aea361-04b3-4a62-bcb0-2f2582f11ec8"
BASE = f"artifacts/BEAAA-41114/full-run/vapi_{AID[:8]}"
API = "http://localhost:3100/api/issues/BEAAA-41114/comments"


def rows(p):
    out = []
    if Path(p).exists():
        for line in Path(p).open():
            try: out.append(json.loads(line))
            except Exception: pass
    return out


def status(d, stem):
    rs = rows(f"{d}/{stem}_rows.jsonl")
    judge = sum(float(j.get("cost_usd") or 0) for r in rs for j in r.get("judges") or [])
    vapi = sum(float(r.get("vapi_cost_usd") or 0) + float(r.get("vapi_cost_usd_b") or 0) for r in rs)
    v = {}
    for r in rs: v[r.get("verdict")] = v.get(r.get("verdict"), 0) + 1
    errs = sum(1 for r in rs if r.get("error"))
    tail = Path(f"{d}/run.log").read_text()[-500:] if Path(f"{d}/run.log").exists() else ""
    return (f"attempts retained {len(rs)}, attempt verdicts {v}, rows with transport error {errs}, "
            f"OpenRouter judge spend ${judge:.4f}, VAPI spend ${vapi:.4f}\n\nlog tail:\n```\n{tail}\n```")


def comment(body):
    req = urllib.request.Request(API, data=json.dumps({"body": body}).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ['PAPERCLIP_API_KEY']}"})
    try: urllib.request.urlopen(req, timeout=30).read(); print("comment posted", flush=True)
    except Exception as exc: print("comment failed", exc, flush=True)


def run(corpus, cap, out, extra=()):
    Path(out).mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "scripts/beaaa38922_conv_live_run.py", "--adapter", "vapi", "--vapi-assistant-id", AID,
           "--corpus", corpus, "--seeds", "42", "43", "44", "--cap-usd", str(cap), "--workers", "4", "--out", out, *extra]
    with open(f"{out}/run.log", "a") as log:
        p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONPATH": "."})
        Path(f"{out}/run.pid").write_text(str(p.pid)); return p.wait()




def alive(pid):
    try: os.kill(pid, 0); return True
    except OSError: return False


# v3's conv-en child keeps running after v3 is stopped; wait for it instead of relaunching it
CHILD = int(sys.argv[2])
while alive(CHILD):
    time.sleep(30)
comment("Driver v4: **VAPI conv-en first pass (2,350 cases, K=3) exited.** "
        + status(f"{BASE}/conv-en", f"conv_live_vapi_{AID[:8]}")
        + "\n\nStarting VAPI OSS run (1,734 cases, K=3, judge cap $12, 4 workers, 429 backoff), "
          "then a conv-en pass that resends only HTTP 429-rejected attempts (429 rows are kept as evidence).")
rc = run("oss", 12, f"{BASE}/oss")
comment(f"Driver v4: **VAPI OSS run (1,734 cases, K=3) exited rc={rc}.** "
        + status(f"{BASE}/oss", f"oss_conv_live_vapi_{AID[:8]}")
        + "\n\nStarting conv-en 429-retry pass (--retry-429).")
rc = run("conv-en", 13, f"{BASE}/conv-en", ("--retry-429",))
comment(f"Driver v4: **VAPI conv-en 429-retry pass exited rc={rc}.** "
        + status(f"{BASE}/conv-en", f"conv_live_vapi_{AID[:8]}")
        + "\n\nAll VAPI text runs complete; next: regenerate the quality report with --vapi-dir and attach to BEAAA-41113.")
