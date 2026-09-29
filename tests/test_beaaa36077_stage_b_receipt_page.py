import json, threading, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from stage_b_pilot.receipt_page import build_server, project_receipts, render_receipts
ROWS=[{"receipt_id":"in_check","kind":"check","correlation_key":"BB-1","body_text":"Check ID: BB-1\nProposed action: Reserve one desk lamp\nTerms: Ask before substitutions\nDeposit: $0.00\nMoney at risk: $48.00 maximum\nYour answer: Approved"},{"receipt_id":"in_confirm","kind":"forwarded_confirmation","correlation_key":"BB-1","body_text":"Check ID: BB-1\nProposed action: Reserve one desk lamp\nTerms: Ask before substitutions\nDeposit: $0.00\nMoney at risk: $48.00 maximum"}]
class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer confirmed-token": self.send_response(401); self.end_headers(); return
        body=json.dumps({"mode":"DEMO","receipts":ROWS}).encode(); self.send_response(200); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,*_): pass
def _servers():
    upstream=ThreadingHTTPServer(("127.0.0.1",0),Upstream); page=build_server("127.0.0.1",0,"127.0.0.1",upstream.server_port,"test-session-secret"); threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (upstream,page)]
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
            body=response.read().decode(); cookie=response.headers["Set-Cookie"]; assert "in_check" in body and "MATCH" in body; assert "sender_address" not in body and "message_sha256" not in body
            for flag in ("HttpOnly", "Secure", "SameSite=Strict"): assert flag in cookie
            assert "confirmed-token" not in cookie and "user@example.com" not in cookie
        request=urllib.request.Request(base+"/stage-b-demo/receipts",headers={"Cookie":cookie.split(";",1)[0]})
        with urllib.request.urlopen(request) as response: assert "in_check" in response.read().decode()
        request=urllib.request.Request(base+"/stage-b-demo/receipts",headers={"Cookie":"bb_demo_session=revoked.invalid"})
        with urllib.request.urlopen(request) as response:
            body=response.read().decode(); assert 'type="password"' in body and "in_check" not in body
        bad=urllib.parse.urlencode({"email":"user@example.com","token":"wrong"}).encode()
        try: urllib.request.urlopen(urllib.request.Request(base+"/stage-b-demo/receipts",data=bad)); assert False
        except urllib.error.HTTPError as error: assert error.code == 401
    finally:
        for server in (page,upstream): server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=5)
def test_no_confirmation_and_mismatch_states():
    assert project_receipts(ROWS[:1])[0].outcome == "NO CONFIRMATION RECEIVED"
    assert project_receipts([ROWS[0],dict(ROWS[1],body_text="Proposed action: Reserve two desk lamps")])[0].outcome == "MISMATCH"

def test_reversed_arrival_and_multiple_open_checks_correlate_by_id():
    check_two=dict(ROWS[0],receipt_id="in_check_2",correlation_key="BB-2",body_text=ROWS[0]["body_text"].replace("BB-1","BB-2").replace("one desk lamp","two desk lamps"))
    confirm_two=dict(ROWS[1],receipt_id="in_confirm_2",correlation_key="BB-2",body_text=ROWS[1]["body_text"].replace("BB-1","BB-2").replace("one desk lamp","two desk lamps"))
    projected=project_receipts([confirm_two,ROWS[1],check_two,ROWS[0]])
    assert [(row.check_id,row.outcome) for row in projected] == [("in_check_2","MATCH"),("in_check","MATCH")]

def test_confirmation_without_matching_explicit_id_never_pairs_by_order():
    unrelated=dict(ROWS[1],correlation_key="BB-other",body_text=ROWS[1]["body_text"].replace("BB-1","BB-other"))
    assert project_receipts([ROWS[0],unrelated])[0].outcome == "NO CONFIRMATION RECEIVED"
