import json, threading, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from stage_b_pilot.receipt_page import build_server, project_receipts, render_receipts
ROWS=[{"receipt_id":"in_check","kind":"check","body_text":"Proposed action: Reserve one desk lamp\nTerms: Ask before substitutions\nDeposit: $0.00\nMoney at risk: $48.00 maximum\nYour answer: Approved"},{"receipt_id":"in_confirm","kind":"forwarded_confirmation","body_text":"Proposed action: Reserve one desk lamp\nTerms: Ask before substitutions\nDeposit: $0.00\nMoney at risk: $48.00 maximum"}]
class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer confirmed-token": self.send_response(401); self.end_headers(); return
        body=json.dumps({"mode":"DEMO","receipts":ROWS}).encode(); self.send_response(200); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,*_): pass
def _servers():
    upstream=ThreadingHTTPServer(("127.0.0.1",0),Upstream); page=build_server("127.0.0.1",0,"127.0.0.1",upstream.server_port); threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (upstream,page)]
    for thread in threads: thread.start()
    return upstream,page,threads
def test_persisted_rows_are_projected_and_sanitized():
    receipt=project_receipts(ROWS)[0]; assert receipt.outcome == "MATCH"; page=render_receipts([receipt])
    for label in ("DEMO PRIVATE","Proposed action","Terms","Deposit","Money at risk","Your answer","Later outcome"): assert label in page
    hostile=dict(ROWS[0],body_text="Proposed action: <script>alert(1)</script>"); assert "<script>" not in render_receipts(project_receipts([hostile]))
def test_authenticated_form_renders_persisted_receipts_with_private_headers():
    upstream,page,threads=_servers(); base=f"http://127.0.0.1:{page.server_port}"
    try:
        with urllib.request.urlopen(base+"/stage-b-demo/receipts") as response:
            assert "no-store" in response.headers["Cache-Control"]; assert response.headers["Referrer-Policy"] == "no-referrer"; assert "noindex" in response.headers["X-Robots-Tag"]; assert b'type="password"' in response.read()
        data=urllib.parse.urlencode({"email":"user@example.com","token":"confirmed-token"}).encode()
        with urllib.request.urlopen(urllib.request.Request(base+"/stage-b-demo/receipts",data=data)) as response:
            body=response.read().decode(); assert "in_check" in body and "MATCH" in body; assert "sender_address" not in body and "message_sha256" not in body
        bad=urllib.parse.urlencode({"email":"user@example.com","token":"wrong"}).encode()
        try: urllib.request.urlopen(urllib.request.Request(base+"/stage-b-demo/receipts",data=bad)); assert False
        except urllib.error.HTTPError as error: assert error.code == 401
    finally:
        for server in (page,upstream): server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=5)
def test_no_confirmation_and_mismatch_states():
    assert project_receipts(ROWS[:1])[0].outcome == "NO CONFIRMATION RECEIVED"
    assert project_receipts([ROWS[0],dict(ROWS[1],body_text="Proposed action: Reserve two desk lamps")])[0].outcome == "MISMATCH"
