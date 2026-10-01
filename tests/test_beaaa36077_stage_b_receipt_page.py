import json, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from stage_b_pilot.receipt_page import CredentialStore, build_server, project_receipts, render_receipts
ROWS=[{"receipt_id":"in_check","kind":"check","correlation_key":"BB-1","body_text":"Check ID: BB-1\nProposed action: Reserve one desk lamp\nTerms: Ask before substitutions\nDeposit: $0.00\nMoney at risk: $48.00 maximum\nYour answer: Approved"},{"receipt_id":"in_confirm","kind":"forwarded_confirmation","correlation_key":"BB-1","body_text":"Check ID: BB-1\nProposed action: Reserve one desk lamp\nTerms: Ask before substitutions\nDeposit: $0.00\nMoney at risk: $48.00 maximum"}]
class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer confirmed-token": self.send_response(401); self.end_headers(); return
        body=json.dumps({"mode":"DEMO","receipts":ROWS}).encode(); self.send_response(200); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        if self.path != "/answers" or self.headers.get("Authorization") != "Bearer confirmed-token": self.send_response(401); self.end_headers(); return
        body=json.dumps({"mode":"DEMO","answer":"OK","delivery_id":"demo-no-delivery"}).encode(); self.send_response(200); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,*_): pass
def _servers(tmp_path, ttl=900, max_sessions=256):
    path=tmp_path/"credentials.json"; path.touch(mode=0o600); path.write_text("{}")
    store=CredentialStore(str(path)); store.provision("user@example.com","correct horse battery","confirmed-token"); store.provision("other@example.com","another safe phrase","other-token")
    upstream=ThreadingHTTPServer(("127.0.0.1",0),Upstream); page=build_server("127.0.0.1",0,"127.0.0.1",upstream.server_port,"test-session-secret",ttl,str(path),max_sessions); threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (upstream,page)]
    for thread in threads: thread.start()
    return upstream,page,threads,store
def test_persisted_rows_are_projected_and_sanitized():
    receipt=project_receipts(ROWS)[0]; assert receipt.outcome == "MATCH"; page=render_receipts([receipt])
    for label in ("DEMO PRIVATE","Check ID / email reference","BB-1","Internal receipt ID","Proposed action","Terms","Deposit","Money at risk","Your answer","Later outcome"): assert label in page
    hostile=dict(ROWS[0],body_text="Proposed action: <script>alert(1)</script>"); assert "<script>" not in render_receipts(project_receipts([hostile]))

def test_identical_actions_keep_distinct_stop_ok_references_and_escape_them():
    first=dict(ROWS[0],receipt_id="in_stop",correlation_key="ARI-STOP",body_text=ROWS[0]["body_text"].replace("BB-1","ARI-STOP"))
    second=dict(ROWS[0],receipt_id='in_ok"><script>alert(1)</script>',correlation_key="ARI-OK",body_text=ROWS[0]["body_text"].replace("BB-1","ARI-OK"))
    page=render_receipts(project_receipts([first,second]))
    assert "ARI-STOP" in page and "ARI-OK" in page
    assert 'name="check_id" value="in_stop"' in page and 'in_ok&quot;&gt;&lt;script&gt;' in page
    assert "<script>" not in page

def test_missing_or_conflicting_reference_prevents_decision():
    missing=dict(ROWS[0],correlation_key=None,body_text="Proposed action: Reserve one desk lamp\nTerms: Ask before substitutions\nDeposit: $0.00\nMoney at risk: $48.00 maximum")
    conflict=dict(ROWS[0],correlation_key="BB-STORED",body_text=ROWS[0]["body_text"].replace("BB-1","BB-BODY"))
    page=render_receipts(project_receipts([missing,conflict]))
    assert page.count("Decision unavailable: the email reference is missing or conflicting.") == 2
    assert 'name="answer"' not in page
def test_authenticated_form_renders_persisted_receipts_with_private_headers(tmp_path):
    upstream,page,threads,store=_servers(tmp_path); base=f"http://127.0.0.1:{page.server_port}"
    try:
        with urllib.request.urlopen(base+"/stage-b-demo/receipts") as response:
            assert "no-store" in response.headers["Cache-Control"]; assert response.headers["Referrer-Policy"] == "no-referrer"; assert "noindex" in response.headers["X-Robots-Tag"]; assert b'type="password"' in response.read()
        data=urllib.parse.urlencode({"email":"user@example.com","passphrase":"correct horse battery"}).encode()
        with urllib.request.urlopen(urllib.request.Request(base+"/stage-b-demo/receipts",data=data)) as response:
            body=response.read().decode(); cookie=response.headers["Set-Cookie"]; assert "in_check" in body and "MATCH" in body; assert "sender_address" not in body and "message_sha256" not in body
            for flag in ("HttpOnly", "Secure", "SameSite=Strict"): assert flag in cookie
            assert "confirmed-token" not in cookie and "user@example.com" not in cookie and "Log out" in body
        request=urllib.request.Request(base+"/stage-b-demo/receipts",headers={"Cookie":cookie.split(";",1)[0]})
        with urllib.request.urlopen(request) as response: assert "in_check" in response.read().decode()
        logout=urllib.request.Request(base+"/stage-b-demo/logout",data=b"",headers={"Cookie":cookie.split(";",1)[0]})
        with urllib.request.urlopen(logout) as response: assert "Max-Age=0" in response.headers["Set-Cookie"]
        request=urllib.request.Request(base+"/stage-b-demo/receipts",headers={"Cookie":cookie.split(";",1)[0]})
        with urllib.request.urlopen(request) as response:
            body=response.read().decode(); assert 'type="password"' in body and "in_check" not in body
        bad=urllib.parse.urlencode({"email":"user@example.com","passphrase":"wrong"}).encode()
        try: urllib.request.urlopen(urllib.request.Request(base+"/stage-b-demo/receipts",data=bad)); assert False
        except urllib.error.HTTPError as error: assert error.code == 401
    finally:
        for server in (page,upstream): server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=5)

def test_expiry_revocation_cross_user_and_session_cap(tmp_path):
    upstream,page,threads,store=_servers(tmp_path,ttl=1,max_sessions=1); base=f"http://127.0.0.1:{page.server_port}/stage-b-demo/receipts"
    def login(email, password):
        return urllib.request.urlopen(urllib.request.Request(base,data=urllib.parse.urlencode({"email":email,"passphrase":password}).encode()))
    try:
        first=login("user@example.com","correct horse battery").headers["Set-Cookie"].split(";",1)[0]
        second=login("user@example.com","correct horse battery").headers["Set-Cookie"].split(";",1)[0]
        with urllib.request.urlopen(urllib.request.Request(base,headers={"Cookie":first})) as response: assert 'type="password"' in response.read().decode()
        with urllib.request.urlopen(urllib.request.Request(base,headers={"Cookie":second})) as response: assert "in_check" in response.read().decode()
        with pytest.raises(urllib.error.HTTPError): login("other@example.com","correct horse battery")
        store.revoke("user@example.com")
        with pytest.raises(urllib.error.HTTPError): login("user@example.com","correct horse battery")
        with urllib.request.urlopen(urllib.request.Request(base,headers={"Cookie":second})) as response: assert 'type="password"' in response.read().decode()
    finally:
        for server in (page,upstream): server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=5)

def test_password_change_invalidates_existing_session_immediately(tmp_path):
    upstream,page,threads,store=_servers(tmp_path); base=f"http://127.0.0.1:{page.server_port}/stage-b-demo/receipts"
    try:
        data=urllib.parse.urlencode({"email":"user@example.com","passphrase":"correct horse battery"}).encode()
        cookie=urllib.request.urlopen(urllib.request.Request(base,data=data)).headers["Set-Cookie"].split(";",1)[0]
        store.provision("user@example.com","replacement safe phrase","confirmed-token")
        with urllib.request.urlopen(urllib.request.Request(base,headers={"Cookie":cookie})) as response:
            assert 'type="password"' in response.read().decode()
    finally:
        for server in (page,upstream): server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=5)
def test_no_confirmation_and_mismatch_states():
    assert project_receipts(ROWS[:1])[0].outcome == "NO CONFIRMATION RECEIVED"
    mismatched=dict(ROWS[1],body_text=ROWS[1]["body_text"].replace("one desk lamp","two desk lamps"))
    assert project_receipts([ROWS[0],mismatched])[0].outcome == "MISMATCH"

def test_reversed_arrival_and_multiple_open_checks_correlate_by_id():
    check_two=dict(ROWS[0],receipt_id="in_check_2",correlation_key="BB-2",body_text=ROWS[0]["body_text"].replace("BB-1","BB-2").replace("one desk lamp","two desk lamps"))
    confirm_two=dict(ROWS[1],receipt_id="in_confirm_2",correlation_key="BB-2",body_text=ROWS[1]["body_text"].replace("BB-1","BB-2").replace("one desk lamp","two desk lamps"))
    projected=project_receipts([confirm_two,ROWS[1],check_two,ROWS[0]])
    assert [(row.check_id,row.outcome) for row in projected] == [("in_check_2","MATCH"),("in_check","MATCH")]

def test_confirmation_without_matching_explicit_id_never_pairs_by_order():
    unrelated=dict(ROWS[1],correlation_key="BB-other",body_text=ROWS[1]["body_text"].replace("BB-1","BB-other"))
    assert project_receipts([ROWS[0],unrelated])[0].outcome == "NO CONFIRMATION RECEIVED"

def test_exact_live_legacy_format_correlates_with_null_database_keys():
    legacy=[]
    for row in ROWS:
        legacy.append(dict(row,correlation_key=None,body_text=row["body_text"].replace("Check ID: BB-1","Test reference: BB-INSTINCT-20260929-01")))
    assert project_receipts(list(reversed(legacy)))[0].outcome == "MATCH"

def test_reused_check_id_and_multiple_distinct_confirmations_are_ambiguous():
    reused=dict(ROWS[0],receipt_id="in_check_reused")
    assert {item.outcome for item in project_receipts([ROWS[0],reused,ROWS[1]])} == {"AMBIGUOUS EVIDENCE"}
    differing=dict(ROWS[1],receipt_id="in_confirm_different",body_text=ROWS[1]["body_text"].replace("one desk lamp","two desk lamps"))
    assert project_receipts([ROWS[0],ROWS[1],differing])[0].outcome == "AMBIGUOUS EVIDENCE"

def test_conflicting_ids_and_missing_required_fields_never_match():
    conflict=dict(ROWS[0],correlation_key=None,body_text=ROWS[0]["body_text"]+"\nTest reference: BB-OTHER")
    assert project_receipts([conflict,ROWS[1]])[0].outcome == "AMBIGUOUS EVIDENCE"
    incomplete=dict(ROWS[1],body_text="Check ID: BB-1\nProposed action: Reserve one desk lamp")
    assert project_receipts([ROWS[0],incomplete])[0].outcome == "INCOMPLETE EVIDENCE"

def test_addendum_four_cancellation_registers_without_approval_or_answer(tmp_path):
    from stage_b_pilot.inbound import InboundStore
    from email.message import EmailMessage
    db=tmp_path/"answers.sqlite3"; store=InboundStore(db); _,token=store.begin_signup("user@example.com"); store.confirm_signup("user@example.com",token)
    store.configure_agent_recipient("user@example.com", token, "registered-agent@example.net")
    assert store.authenticate("user@example.com", token)
    with pytest.raises(PermissionError, match="enrollment_authentication_invalid"):
        store.configure_agent_recipient("user@example.com", "wrong-token", "attacker@example.net")
    message=EmailMessage(); message["From"]="user@example.com"; message["To"]="check@inbound.backbond.ai"; message["Subject"]="BackBond check"; message.set_content("Check ID: BB-4\nProposed action: Cancel the demo booking\nTerms: demo only\nMoney at risk: $0.00\nTask limit: over\nObligation status: open")
    store.ingest(message.as_bytes()); row=store.receipts_for("user@example.com")[0]; receipt=project_receipts([row])[0]
    assert receipt.user_answer == "No response required"
    assert receipt.limit_state == "over your limit" and receipt.obligation_status == "open"
    assert receipt.response_required is False and receipt.decision_allowed is False
    page=render_receipts([receipt])
    assert 'name="answer"' not in page and "BackBond APPROVED" not in page
    assert "No OK or STOP response is required for a cancellation check." in page
    assert "Cancellation obligation registered; reminders started" in page
    store.close()


def test_saved_answer_without_delivery_is_labelled_truthfully():
    row = dict(ROWS[0], authenticated_answer={"answer": "OK", "delivery_id": None})
    page = render_receipts(project_receipts([row]))
    assert "Answer saved; not sent to the agent" in page
    assert "Email accepted for sending" not in page

    demo_row = dict(ROWS[0], authenticated_answer={"answer": "STOP", "delivery_id": "demo-no-delivery"})
    demo_page = render_receipts(project_receipts([demo_row]))
    assert "Answer saved; not sent to the agent" in demo_page
    assert "Email accepted for sending" not in demo_page


def test_answer_route_discloses_demo_no_delivery(tmp_path):
    upstream,page,threads,store=_servers(tmp_path); base=f"http://127.0.0.1:{page.server_port}"
    try:
        login=urllib.parse.urlencode({"email":"user@example.com","passphrase":"correct horse battery"}).encode()
        cookie=urllib.request.urlopen(urllib.request.Request(base+"/stage-b-demo/receipts",data=login)).headers["Set-Cookie"].split(";",1)[0]
        answer=urllib.parse.urlencode({"check_id":"in_check","answer":"OK"}).encode()
        request=urllib.request.Request(base+"/stage-b-demo/answer",data=answer,headers={"Cookie":cookie})
        with urllib.request.urlopen(request) as response:
            body=response.read().decode()
            assert "Answer saved; it has not been sent to the agent" in body
            assert "Answer saved and sent to the agent" not in body
    finally:
        for server in (page,upstream): server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=5)
