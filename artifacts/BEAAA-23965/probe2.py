#!/usr/bin/env python3
"""QA BEAAA-23965 technical-layer probes the shipped suites do not cover:
  A  agentfile _b64pad change vs v5 '+==' across every b64 length class
  B  agentbundle parity (the parity suite omits this route entirely)
  C  audit-write failure must not break a request
  D  hostile / unicode / oversize inputs must not 500
  E  wstat on an empty session must match the DEPLOY.md step-7 claim
  F  WMAX session cap
Serves v5 on 8296 and the candidate on 8297 against a throwaway agents root.
"""
import base64, importlib.util, json, os, shutil, subprocess, sys, tempfile, threading, time
import urllib.error, urllib.request
from http.server import ThreadingHTTPServer

V5 = "/opt/paperclip-bridge/bridge.py"
V6 = ("/mnt/paperclipdata/dot-paperclip/instances/default/workspaces/"
      "88e68019-1cb6-4c4f-83e1-9fd340fb6821/artifacts/beaaa-23961/bridge.py")
TOKEN = "q" * 24
UUID = "11111111-2222-3333-4444-555555555555"
ROOT = tempfile.mkdtemp(prefix="qa23965-agents-")
INSTR = os.path.join(ROOT, UUID, "instructions")
os.makedirs(INSTR)

# filenames chosen so urlsafe-b64(name) has length %4 == 0,1,2,3
NAMES = {}
for target in (0, 1, 2, 3):
    for L in range(1, 40):
        n = ("f" * L) + ".md"
        e = base64.urlsafe_b64encode(n.encode()).decode().rstrip("=")
        if len(e) % 4 == target and target not in NAMES:
            NAMES[target] = (n, e)
            break
for t, (n, e) in sorted(NAMES.items()):
    with open(os.path.join(INSTR, n), "w") as f:
        f.write("content of %s\n" % n)
    print(f"  fixture len%4=={t}  name={n!r}  b64={e!r} (len {len(e)})")


def serve(src, port, audit):
    os.environ.update(BRIDGE_TOKEN=TOKEN, BRIDGE_PORT=str(port),
                      BRIDGE_UPSTREAM="http://127.0.0.1:3100",
                      BRIDGE_AGENTS_ROOT=ROOT, BRIDGE_AUDIT_LOG=audit,
                      BRIDGE_WMAX_SESSIONS="4")
    spec = importlib.util.spec_from_file_location("b%d" % port, src)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", port), m.H)
                     .serve_forever, daemon=True).start()
    return m


def get(port, path, token=TOKEN, raw=False):
    url = f"http://127.0.0.1:{port}/{token}/{path}" if not raw else \
          f"http://127.0.0.1:{port}/{path}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return -1, repr(e)[:90].encode()


BAD = []


def ck(name, ok, detail):
    BAD.append(name) if not ok else None
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")


m5 = serve(V5, 8296, "/tmp/qa23965/p2-v5.log")
m6 = serve(V6, 8297, "/tmp/qa23965/p2-v6.log")
time.sleep(0.4)

print("\n### A  agentfile: _b64pad(v6) vs '+=='(v5), every b64 length class")
for t, (n, e) in sorted(NAMES.items()):
    s1, b1 = get(8296, f"agentfile/{UUID}/{e}")
    s2, b2 = get(8297, f"agentfile/{UUID}/{e}")
    ck(f"A{t} agentfile b64len%4=={t}", (s1, b1) == (s2, b2),
       f"v5={s1}/{b1[:26]!r}  v6={s2}/{b2[:26]!r}")

print("\n### B  agentbundle / agentlist parity (not covered by the parity suite)")
for route in (f"agentbundle/{UUID}", f"agentlist/{UUID}",
              "agentbundle/nosuchuuid", "agentbundle/..",
              f"agentfile/{UUID}/{base64.urlsafe_b64encode(b'/etc/passwd').decode().rstrip('=')}",
              f"agentfile/{UUID}/!!!notb64!!!"):
    s1, b1 = get(8296, route)
    s2, b2 = get(8297, route)
    ck(f"B {route[:44]}", (s1, b1) == (s2, b2),
       f"v5={s1}/{len(b1)}b  v6={s2}/{len(b2)}b")

print("\n### C  audit-write failure must not break the request")
m6.AUDIT_PATH = "/proc/1/nonexistent/audit.log"     # unwritable, undirectory-able
s, b = get(8297, "wnew")
ck("C1 wnew still serves when the audit sink is unwritable", s == 200 and len(b) == 22,
   f"-> {s} {b[:26]!r}")
sid = b.decode()
s2, b2 = get(8297, f"wput/{sid}/0/QUFB")
ck("C2 wput still serves when the audit sink is unwritable",
   s2 == 200 and b2.startswith(b"ok 1 "), f"-> {s2} {b2!r}")
m6.AUDIT_PATH = "/tmp/qa23965/p2-v6.log"

print("\n### D  hostile input must be a status, never a 500 / dropped connection")
cases = [
    ("unicode-digit declared n", f"wgo/{sid}/%C2%B2%C2%B2/abcdefabcdef/api/x"),
    ("huge declared n", f"wgo/{sid}/{'9'*400}/abcdefabcdef/api/x"),
    ("declared sha uppercase", f"wgo/{sid}/1/ABCDEFABCDEF/api/x"),
    ("wput seq negative", f"wput/{sid}/-5/QUFB"),
    ("wput seq huge", f"wput/{sid}/{'9'*300}/QUFB"),
    ("wput seq float", f"wput/{sid}/1.5/QUFB"),
    ("wstat unknown", "wstat/nosuchsid"),
    ("wshow unknown", "wshow/nosuchsid"),
    ("wgo no path", f"wgo/{sid}/"),
    ("wnew with junk tail", "wnew/../../etc"),
    ("empty verb", ""),
]
for name, path in cases:
    s, b = get(8297, path)
    ck(f"D {name}", s not in (-1, 500), f"-> {s} {b[:60]!r}")
s, b = get(8297, "ping", token="w" * 24)
ck("D wrong token", s == 403, f"-> {s} {b[:20]!r}")
s, b = get(8297, "%C3%A9%00/ping", raw=True)
ck("D non-ascii token", s == 403, f"-> {s} {b[:20]!r}")

print("\n### E  DEPLOY.md step 7 claim: wstat on an empty session")
esid = get(8297, "wnew")[1].decode()
s, b = get(8297, f"wgo/{esid}/api/companies/X/issues")
ck("E1 empty wgo -> 400 with the runbook's wording",
   s == 400 and b.startswith(b"empty session: no slices stored"), f"-> {s} {b!r}")
s, b = get(8297, f"wstat/{esid}")
j = json.loads(b)
ck("E2 wstat shows n=0, assembles=false", j["n"] == 0 and j["assembles"] is False,
   json.dumps({k: j[k] for k in ("sid", "n", "seqs", "assembles", "committed")}))
ck("E3 wstat does not echo any body", "body" not in json.dumps(j).lower()
   or j["body_bytes"] is None, f"body_bytes={j['body_bytes']} body_sha256_12={j['body_sha256_12']}")

print("\n### F  session cap (BRIDGE_WMAX_SESSIONS=4)")
codes = [get(8297, "wnew")[0] for _ in range(8)]
ck("F1 wnew refuses past the cap with 503, not a crash",
   503 in codes and -1 not in codes and 500 not in codes, f"codes {codes}")

print(f"\n{'ALL PASS' if not BAD else 'FAILED: ' + ', '.join(BAD)}")
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(1 if BAD else 0)
