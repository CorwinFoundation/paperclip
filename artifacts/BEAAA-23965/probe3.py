#!/usr/bin/env python3
"""QA BEAAA-23965 — isolated retest of the v6 declared-form parser.

Correcting a probe error: probe2 pointed BRIDGE_UPSTREAM at the real API on
:3100, so its 'unicode-digit' case relayed to the real upstream and the 500 came
from there, not the bridge. Everything here uses a stub upstream, and the raw
socket path exercises bytes curl would percent-encode.

Question under test: v6 line 274 gates the declared form on bits[0].isdigit(),
then line 275 calls int(bits[0]). str.isdigit() is True for characters int()
rejects (superscripts, e.g. U+00B2). BaseHTTPRequestHandler decodes the request
line as iso-8859-1, so a raw 0xB2 byte reaches self.path as U+00B2.
"""
import base64, importlib.util, json, os, socket, threading, time
import urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

V6 = ("/mnt/paperclipdata/dot-paperclip/instances/default/workspaces/"
      "88e68019-1cb6-4c4f-83e1-9fd340fb6821/artifacts/beaaa-23961/bridge.py")
TOKEN = "r" * 24
BR, UP = 8294, 8295
POSTS = []


class Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        POSTS.append((self.path, self.rfile.read(n)))
        o = b'{"stub":true}'
        self.send_response(201); self.send_header("Content-Length", str(len(o)))
        self.end_headers(); self.wfile.write(o)

    def log_message(self, *a): pass


threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", UP), Stub)
                 .serve_forever, daemon=True).start()
os.environ.update(BRIDGE_TOKEN=TOKEN, BRIDGE_PORT=str(BR),
                  BRIDGE_UPSTREAM=f"http://127.0.0.1:{UP}",
                  BRIDGE_AUDIT_LOG="/tmp/qa23965/p3.log")
spec = importlib.util.spec_from_file_location("b6", V6)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", BR), m.H)
                 .serve_forever, daemon=True).start()
time.sleep(0.4)


def get(path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{BR}/{TOKEN}/{path}",
                                    timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return -1, repr(e)[:70].encode()


def raw(pathbytes):
    """Send a request line with raw bytes, the way curl will not."""
    s = socket.create_connection(("127.0.0.1", BR), timeout=15)
    s.sendall(b"GET " + pathbytes + b" HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    buf = b""
    try:
        while True:
            c = s.recv(65536)
            if not c:
                break
            buf += c
    except Exception as e:
        buf += b"<<recv error: %s>>" % repr(e).encode()
    s.close()
    return buf.split(b"\r\n", 1)[0] if buf else b"<<no response: connection closed>>"


sid = get("wnew")[1].decode()
body = json.dumps({"qa": "23965"}).encode()
b64 = base64.urlsafe_b64encode(body).decode().rstrip("=")
get(f"wput/{sid}/0/{b64}")
sha12 = __import__("hashlib").sha256(body).hexdigest()[:12]
print(f"sid={sid} n=1 sha12={sha12}  upstream POSTs so far: {len(POSTS)}")

T = TOKEN.encode()
print("\n### percent-encoded (what curl sends) — NOT the declared form, relays as a path")
n0 = len(POSTS)
st, b = get(f"wgo/{sid}/%C2%B2%C2%B2/abcdefabcdef/api/x")
print(f"  -> {st} {b[:60]!r}; upstream POSTs +{len(POSTS)-n0}"
      f"  path={POSTS[-1][0] if len(POSTS)>n0 else '-'}")

print("\n### raw 0xB2 bytes in the request line (reachable by any raw-socket client)")
sid2 = get("wnew")[1].decode()
get(f"wput/{sid2}/0/{b64}")
n1 = len(POSTS)
r = raw(b"/" + T + b"/wgo/" + sid2.encode() + b"/\xb2\xb2/abcdefabcdef/api/x")
print(f"  status line: {r!r}")
print(f"  upstream POSTs +{len(POSTS)-n1}")
print(f"  session still present: {sid2 in m.WSESS}; "
      f"in_flight={m.WSESS.get(sid2,{}).get('in_flight')}")

print("\n### same shape with an ASCII digit (the intended declared form) — control")
n2 = len(POSTS)
r2 = raw(b"/" + T + b"/wgo/" + sid2.encode() + b"/1/" + sha12.encode() + b"/api/x")
print(f"  status line: {r2!r}; upstream POSTs +{len(POSTS)-n2}")

print("\n### other str.isdigit()-True-but-int()-False characters")
for label, ch in [("superscript two U+00B2", b"\xb2"),
                  ("superscript three U+00B3", b"\xb3"),
                  ("superscript one U+00B9", b"\xb9")]:
    sidx = get("wnew")[1].decode()
    get(f"wput/{sidx}/0/{b64}")
    n = len(POSTS)
    rr = raw(b"/" + T + b"/wgo/" + sidx.encode() + b"/" + ch + b"/abcdefabcdef/api/x")
    print(f"  {label}: {rr!r}  upstream POSTs +{len(POSTS)-n}")

print("\n### does the same raw byte 500 the BASELINE v5? (regression scope)")
os.environ.update(BRIDGE_TOKEN=TOKEN, BRIDGE_PORT="8293",
                  BRIDGE_UPSTREAM=f"http://127.0.0.1:{UP}")
spec5 = importlib.util.spec_from_file_location("b5", "/opt/paperclip-bridge/bridge.py")
m5 = importlib.util.module_from_spec(spec5); spec5.loader.exec_module(m5)
threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", 8293), m5.H)
                 .serve_forever, daemon=True).start()
time.sleep(0.3)


def raw5(pathbytes):
    s = socket.create_connection(("127.0.0.1", 8293), timeout=15)
    s.sendall(b"GET " + pathbytes + b" HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    buf = b""
    while True:
        c = s.recv(65536)
        if not c:
            break
        buf += c
    s.close()
    return buf.split(b"\r\n", 1)[0] if buf else b"<<no response>>"


try:
    with urllib.request.urlopen(f"http://127.0.0.1:8293/{TOKEN}/wnew", timeout=10) as r:
        sid5 = r.read().decode()
    n = len(POSTS)
    print(f"  v5 raw 0xB2: {raw5(b'/'+T+b'/wgo/'+sid5.encode()+b'/\\xb2\\xb2/abcdefabcdef/api/x')!r}"
          f"  upstream POSTs +{len(POSTS)-n}")
except Exception as e:
    print(f"  v5 probe error: {e!r}")

print(f"\ntotal upstream POSTs: {len(POSTS)}")
for p, bb in POSTS:
    print(f"  {p}  {len(bb)}b {bb[:40]!r}")
