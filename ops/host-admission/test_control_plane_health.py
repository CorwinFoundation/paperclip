#!/usr/bin/env python3
"""BEAAA-24049 -- sabotage tests for control-plane-health.sh.

The probe already reports HEALTHY against the live server, but that only shows
it can agree with a working host. The claim that matters is discrimination: it
must report DEGRADED in the exact state where PM2 reported `online` throughout
BEAAA-24046, and it must not restart-signal a server that is merely slow.

Each case stands up a stub HTTP server with a controlled failure shape, runs the
real shipped probe against it, and asserts the exit code and state. Case E is
the load-bearing one: it reproduces the outage signature (root fast, DB path
hung) and asserts that a naive `/`-probe calls it healthy while the shipped
probe does not.
"""

import http.server
import json
import os
import socket
import subprocess
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "control-plane-health.sh")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def make_handler(router_code=200, ready_code=200, ready_delay=0.0, root_delay=0.0):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body=b"{}"):
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                time.sleep(root_delay)
                return self._send(200)
            if self.path == "/api/health":
                if router_code != 200:
                    return self._send(router_code)
                return self._send(200)
            # the DB-backed readiness path
            time.sleep(ready_delay)
            return self._send(ready_code)

    return H


class Stub:
    def __init__(self, **kw):
        self.port = free_port()
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), make_handler(**kw))
        self.srv.daemon_threads = True

    def __enter__(self):
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


def run_probe(port, ready_max="10", warn="2"):
    env = dict(os.environ)
    env.update({
        "PAPERCLIP_LISTEN_PORT": str(port),
        "PAPERCLIP_LISTEN_HOST": "127.0.0.1",
        "READY_MAX_S": ready_max,
        "ROUTER_MAX_S": "10",
        "READY_WARN_S": warn,
        "READY_PATH": "/api/issues/PROBE",
        "PAPERCLIP_API_KEY": "test-token",
    })
    r = subprocess.run([PROBE], env=env, capture_output=True, text=True, timeout=180)
    try:
        payload = json.loads(r.stdout.strip() or "{}")
    except json.JSONDecodeError:
        payload = {"_raw": r.stdout}
    return r.returncode, payload


results = []


def check(name, got, want):
    ok = got == want
    results.append((name, ok, f"got={got} want={want}"))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got} want={want}")
    return ok


# A -- nothing bound at all. Must be exit 3, not a hang or a false HEALTHY.
port = free_port()
rc, out = check_a = run_probe(port)
check("A no listener -> DOWN(3)", (rc, out.get("state"), out.get("tier")), (3, "DOWN", "L1"))

# B -- listener bound, router broken (503). Restart candidate, exit 2.
with Stub(router_code=503) as s:
    rc, out = run_probe(s.port)
check("B router 503 -> DOWN(2)", (rc, out.get("state"), out.get("tier")), (2, "DOWN", "L2"))

# C -- everything fast. Exit 0.
with Stub() as s:
    rc, out = run_probe(s.port)
check("C all fast -> HEALTHY(0)", (rc, out.get("state")), (0, "HEALTHY"))

# D -- serving, but the DB path is slower than the warn band. DEGRADED, not DOWN:
#      this must NOT produce a restart signal.
with Stub(ready_delay=4.0) as s:
    rc, out = run_probe(s.port, ready_max="30", warn="2")
check("D slow ready -> DEGRADED(1)", (rc, out.get("state"), out.get("reason")),
      (1, "DEGRADED", "serving but starved"))

# E -- THE OUTAGE SIGNATURE. Root answers instantly, DB path never completes.
#      This is what PM2 called `online` for 7 minutes.
with Stub(ready_delay=60.0) as s:
    naive_code = None
    t0 = time.time()
    try:
        naive_code = urllib.request.urlopen(f"http://127.0.0.1:{s.port}/", timeout=5).status
    except Exception as e:
        naive_code = f"err:{e}"
    naive_s = time.time() - t0
    rc, out = run_probe(s.port, ready_max="6", warn="2")

check("E outage sig: naive '/' probe says 200 (false-quiet)", naive_code, 200)
check("E outage sig: shipped probe says DEGRADED(1)",
      (rc, out.get("state"), out.get("tier")), (1, "DEGRADED", "L3"))
print(f"      naive '/' answered in {naive_s:.3f}s while the DB path was hung -- "
      f"this is the discrimination the PM2 `online` signal lacked")

# F -- DEGRADED verdicts must carry actionable context, else the warden cannot
#      decide to shed rather than restart.
for k in ("load", "swap_used_mb", "agent_seats", "io_full_avg60"):
    check(f"F DEGRADED payload carries '{k}'", k in out, True)

# G -- readiness must not be silently downgraded to L2 when no token is present.
with Stub() as s:
    env = dict(os.environ)
    env.update({
        "PAPERCLIP_LISTEN_PORT": str(s.port), "PAPERCLIP_LISTEN_HOST": "127.0.0.1",
        "READY_MAX_S": "10", "ROUTER_MAX_S": "10", "READY_WARN_S": "2",
    })
    env.pop("PAPERCLIP_API_KEY", None)
    r = subprocess.run([PROBE], env=env, capture_output=True, text=True, timeout=60)
    out_g = json.loads(r.stdout.strip() or "{}")
check("G no token -> DEGRADED not HEALTHY", (r.returncode, out_g.get("state")), (1, "DEGRADED"))

failed = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED: " + "; ".join(failed))
raise SystemExit(1 if failed else 0)
