"""Private authenticated HTML view over persisted Stage B DEMO receipts."""
from __future__ import annotations
import argparse, getpass, hashlib, hmac, html, json, os, secrets, time
from dataclasses import dataclass
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote, urlparse
from .correlation import correlation_key

class CredentialStore:
    """Mode-0600 mapping with salted scrypt passphrase hashes."""
    def __init__(self, path: str): self.path = path
    def _read(self):
        try:
            if os.stat(self.path).st_mode & 0o077: raise PermissionError("credential file must be mode 0600")
            with open(self.path, encoding="utf-8") as source: return json.load(source)
        except FileNotFoundError: return {}
    def verify(self, email: str, passphrase: str):
        row = self._read().get(email.strip().lower())
        if not row or row.get("revoked"): return None
        try: actual = hashlib.scrypt(passphrase.encode(), salt=bytes.fromhex(row["salt"]), n=16384, r=8, p=1).hex()
        except (KeyError, TypeError, ValueError): return None
        if not hmac.compare_digest(actual, row.get("digest", "")): return None
        return row["upstream_token"], int(row.get("generation", 0))
    def active(self, email: str, token: str, generation: int) -> bool:
        row = self._read().get(email.strip().lower())
        return bool(row and not row.get("revoked") and
                    hmac.compare_digest(str(row.get("upstream_token", "")), token) and
                    int(row.get("generation", 0)) == generation)
    def _write(self, rows):
        temporary = self.path + ".new"; descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as target: json.dump(rows, target, sort_keys=True)
        os.replace(temporary, self.path)
    def provision(self, email: str, passphrase: str, upstream_token: str):
        if len(passphrase) < 12 or not upstream_token: raise ValueError("passphrase must be at least 12 characters and token non-empty")
        rows = self._read(); salt = secrets.token_bytes(16)
        address = email.strip().lower(); generation = int(rows.get(address, {}).get("generation", 0)) + 1
        rows[address] = {"salt":salt.hex(), "digest":hashlib.scrypt(passphrase.encode(), salt=salt, n=16384, r=8, p=1).hex(), "upstream_token":upstream_token, "revoked":False, "generation":generation}; self._write(rows)
    def revoke(self, email: str):
        rows = self._read(); address = email.strip().lower()
        if address in rows: rows[address]["revoked"] = True
        self._write(rows)

@dataclass(frozen=True)
class DemoReceipt:
    check_id: str; proposed_action: str; terms: str; deposit: str
    money_at_risk: str; user_answer: str; outcome: str; outcome_detail: str
    limit_state: str = "limit not set"; obligation_status: str = "open"
    relay_status: str = "No answer sent"

def _structured_body(body: str) -> dict[str, str]:
    allowed = {"check id":"correlation_key", "backbond check id":"correlation_key", "correlation id":"correlation_key", "proposed action":"proposed_action", "terms":"terms", "deposit":"deposit", "money at risk":"money_at_risk", "your answer":"user_answer", "user answer":"user_answer", "task limit":"task_limit", "obligation status":"obligation_status"}
    values = {}
    for line in body.splitlines():
        key, separator, value = line.partition(":")
        field = allowed.get(key.strip().lower())
        if separator and field and value.strip(): values[field] = value.strip()[:500]
    return values

def project_receipts(rows: list[dict[str, object]]) -> list[DemoReceipt]:
    projected = []
    confirmations: dict[str, list[dict[str, object]]] = {}
    check_counts: dict[str, int] = {}
    for row in rows:
        try:
            parsed = correlation_key(str(row.get("body_text", "")))
            stored = row.get("correlation_key")
            if stored and parsed and str(stored) != parsed: raise ValueError("correlation_identifier_conflict")
            key = str(stored or parsed or "") or None
        except ValueError:
            key = None
            pass
        if row.get("kind") == "check" and key: check_counts[key] = check_counts.get(key, 0) + 1
        if row.get("kind") == "forwarded_confirmation" and key: confirmations.setdefault(key, []).append(row)
    for row in (row for row in rows if row.get("kind") == "check"):
        fields = _structured_body(str(row.get("body_text", "")))
        try:
            parsed = correlation_key(str(row.get("body_text", "")))
            stored = row.get("correlation_key")
            if stored and parsed and str(stored) != parsed: raise ValueError
            key = str(stored or parsed or "") or None
        except ValueError:
            key = None
        candidates = confirmations.get(key, []) if key else []
        if (not key) or check_counts.get(key, 0) > 1 or len(candidates) > 1:
            outcome, detail = "AMBIGUOUS EVIDENCE", "The check identifier or confirmation evidence is ambiguous."
        elif not candidates:
            outcome, detail = "NO CONFIRMATION RECEIVED", "No independent confirmation has arrived for this check."
        else:
            confirmed = _structured_body(str(candidates[0].get("body_text", "")))
            required = {"proposed_action", "terms", "deposit", "money_at_risk"}
            if not required.issubset(fields) or not required.issubset(confirmed):
                outcome, detail = "INCOMPLETE EVIDENCE", "Required comparison fields are missing."
            else:
                matches = all(confirmed[name] == fields[name] for name in required)
                outcome = "MATCH" if matches else "MISMATCH"
                detail = "The observed result matched the submitted check." if matches else "The observed result differed from the submitted check."
        stored = row.get("authenticated_answer") or {}
        answer = str(stored.get("answer") or fields.get("user_answer", "Not provided"))
        limit = fields.get("task_limit", "limit not set")
        limit_state = "over your limit" if limit.lower() == "over" else "limit not set" if limit.lower() in {"", "not set"} else limit
        obligation = fields.get("obligation_status", "open").lower()
        if obligation not in {"open", "cancelled", "missed"}: obligation = "open"
        delivery_id = stored.get("delivery_id")
        was_sent = bool(delivery_id and delivery_id != "demo-no-delivery")
        relay_status = "Email accepted for sending; agent acknowledgement not yet observed" if was_sent else "Answer saved; not sent to the agent" if stored else "No answer sent"
        projected.append(DemoReceipt(str(row.get("receipt_id", "Recorded check"))[:80], fields.get("proposed_action", "Not provided"), fields.get("terms", "Not provided"), fields.get("deposit", "Not provided"), fields.get("money_at_risk", "Not provided"), answer, outcome, detail, limit_state, obligation, relay_status))
    return projected

def _page(content: str) -> str:
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><meta name="referrer" content="no-referrer"><title>Private receipts | BackBond</title><style>
:root{{--ink:#0c2030;--paper:#f7f8f6;--copper:#9a4f24;--line:#cbd3d6;--muted:#52636c;--white:#fff;font-family:Inter,Arial,sans-serif;color:var(--ink);background:var(--paper);color-scheme:light}}*{{box-sizing:border-box}}body{{margin:0}}header{{min-height:68px;padding:1rem max(20px,calc((100vw - 980px)/2));display:flex;align-items:center;justify-content:space-between;gap:1rem;border-bottom:1px solid var(--line);background:var(--white)}}.brand{{font:800 1.25rem Archivo,Arial,sans-serif}}.demo{{border:2px solid var(--copper);padding:.4rem .7rem;font:800 .8rem 'IBM Plex Mono',monospace;letter-spacing:.08em}}main{{width:min(100% - 40px,980px);margin:0 auto;padding:clamp(2rem,6vw,5rem) 0}}.intro{{display:grid;grid-template-columns:minmax(0,1fr) 15rem;gap:3rem;align-items:end;border-bottom:3px solid var(--ink);padding-bottom:2rem}}h1{{font:800 clamp(2.25rem,7vw,5rem)/.98 Archivo,Arial,sans-serif;letter-spacing:-.045em;margin:0}}h2{{font:800 clamp(1.25rem,3vw,1.7rem) Archivo,Arial,sans-serif;margin:0}}p{{line-height:1.55}}.privacy,.muted{{color:var(--muted)}}.receipt{{padding:2rem 0;border-bottom:3px solid var(--ink)}}.outcome{{padding:1rem 0;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:1rem;align-items:center}}.outcome span{{color:var(--copper);font:800 .84rem 'IBM Plex Mono',monospace;text-align:right}}dl{{margin:0;display:grid;grid-template-columns:13rem 1fr}}dt,dd{{margin:0;padding:1.1rem 0;border-bottom:1px solid var(--line)}}dt{{color:var(--muted)}}dd{{font-weight:700;overflow-wrap:anywhere}}form{{max-width:34rem;margin-top:2rem}}label{{display:block;font-weight:700;margin-top:1rem}}input{{width:100%;margin-top:.45rem;border:1px solid #64737a;border-radius:4px;background:var(--white);color:var(--ink);font:inherit;padding:.8rem}}button{{margin-top:1.25rem;border:0;border-radius:4px;background:var(--ink);color:var(--white);font:800 1rem Archivo,Arial,sans-serif;padding:.85rem 1.1rem;cursor:pointer}}button:active{{transform:translateY(1px)}}.error{{border-left:4px solid var(--copper);padding:.75rem 1rem;background:var(--white)}}footer{{margin-top:2rem;color:var(--muted);font-size:.875rem}}:focus-visible{{outline:3px solid #2877ad;outline-offset:3px}}@media(max-width:640px){{header{{align-items:flex-start;flex-direction:column}}main{{width:min(100% - 32px,980px);padding:2rem 0}}.intro{{display:block}}.privacy{{margin-top:1rem}}.outcome{{align-items:flex-start;flex-direction:column}}.outcome span{{text-align:left}}dl{{grid-template-columns:1fr}}dt{{padding-bottom:.25rem;border-bottom:0}}dd{{padding-top:0}}}}</style></head><body><header><span class="brand">BackBond</span><span class="demo">DEMO PRIVATE</span></header><main>{content}<footer>DEMO data only. No payment or account connection.</footer></main></body></html>'''

def render_login(error: str = "") -> str:
    message = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return _page(f'''<div class="intro"><h1>Observation receipts</h1><p class="privacy">Sign in with your private pilot passphrase.</p></div>{message}<form method="post" action="/stage-b-demo/receipts" autocomplete="off"><label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email" required><label for="passphrase">Passphrase</label><input id="passphrase" name="passphrase" type="password" autocomplete="current-password" required><button type="submit">View receipts</button></form>''')

def render_receipts(receipts: list[DemoReceipt]) -> str:
    if not receipts: body = '<p class="muted" role="status">No checks have been received for this account yet.</p>'
    else:
        blocks = []
        for receipt in receipts:
            v = {name:html.escape(str(value)) for name,value in receipt.__dict__.items()}
            blocks.append(f'''<article class="receipt"><div class="outcome" role="status"><h2>{v['outcome_detail']}</h2><span>{v['outcome']}</span></div><dl><dt>Check</dt><dd>{v['check_id']}</dd><dt>Proposed action</dt><dd>{v['proposed_action']}</dd><dt>Terms</dt><dd>{v['terms']}</dd><dt>Deposit</dt><dd>{v['deposit']}</dd><dt>Money at risk</dt><dd>{v['money_at_risk']}</dd><dt>Per-task limit</dt><dd>{v['limit_state']}</dd><dt>Your answer</dt><dd>{v['user_answer']}</dd><dt>Relay status</dt><dd>{v['relay_status']}</dd><dt>Obligation</dt><dd>{v['obligation_status']}</dd><dt>Later outcome</dt><dd>{v['outcome']}</dd></dl><form method="post" action="/stage-b-demo/answer"><input type="hidden" name="check_id" value="{v['check_id']}"><button name="answer" value="OK" type="submit">OK</button> <button name="answer" value="STOP" type="submit">STOP</button></form></article>''')
        body = "".join(blocks)
    return _page(f'<div class="intro"><h1>Observation receipts</h1><p class="privacy">Private persisted records for this confirmed pilot account.</p></div><form method="post" action="/stage-b-demo/logout"><button type="submit">Log out</button></form>{body}')

def fetch_receipts(host: str, port: int, email: str, token: str) -> list[dict[str, object]]:
    connection = HTTPConnection(host, port, timeout=5)
    try:
        connection.request("GET", f"/receipts?email={quote(email)}", headers={"Authorization":f"Bearer {token}"})
        response = connection.getresponse(); payload = json.loads(response.read())
    finally: connection.close()
    if response.status != 200: raise PermissionError
    return payload.get("receipts", [])

def submit_answer(host: str, port: int, email: str, token: str, check_id: str, answer: str) -> str:
    body = __import__("urllib.parse").parse.urlencode({"email":email, "check_id":check_id, "answer":answer})
    connection = HTTPConnection(host, port, timeout=5)
    try:
        connection.request("POST", "/answers", body, {"Authorization":f"Bearer {token}", "Content-Type":"application/x-www-form-urlencoded"})
        response = connection.getresponse(); payload = response.read()
    finally: connection.close()
    if response.status == 200:
        result = json.loads(payload)
        return "sent" if result.get("provider_accepted") is True and result.get("delivery_id") else "saved_not_sent"
    if response.status in {202, 502}: return "saved_not_sent"
    raise PermissionError

def make_handler(upstream_host: str, upstream_port: int, session_secret: str | None = None,
                 session_ttl: int = 900, credential_file: str | None = None, max_sessions: int = 256) -> type[BaseHTTPRequestHandler]:
    if session_ttl < 1: raise ValueError("session TTL must be positive")
    if max_sessions < 1: raise ValueError("max sessions must be positive")
    sessions: dict[str, tuple[int, str, str, int]] = {}; credentials = CredentialStore(credential_file) if credential_file else None
    def purge_expired():
        now = int(time.time())
        for key in [key for key, value in sessions.items() if value[0] <= now]: sessions.pop(key, None)
    def make_room():
        purge_expired()
        while len(sessions) >= max_sessions: sessions.pop(min(sessions, key=lambda key:sessions[key][0]), None)

    def signed_session(session_id: str) -> str:
        signature = hmac.new((session_secret or "").encode(), session_id.encode(), hashlib.sha256).hexdigest()
        return f"{session_id}.{signature}"

    def session_credentials(value: str) -> tuple[str, str] | None:
        if session_secret is None: return None
        try: session_id, supplied = value.rsplit(".", 1)
        except ValueError: return None
        if not hmac.compare_digest(signed_session(session_id).rsplit(".", 1)[1], supplied): return None
        purge_expired(); record = sessions.get(session_id)
        if record is None or record[0] <= int(time.time()):
            sessions.pop(session_id, None)
            return None
        if credentials is None or not credentials.active(record[1], record[2], record[3]):
            sessions.pop(session_id, None)
            return None
        return record[1], record[2]

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: str, cookie: str | None = None) -> None:
            encoded = body.encode(); self.send_response(status)
            for name,value in (("Content-Type","text/html; charset=utf-8"),("Content-Length",str(len(encoded))),("Cache-Control","private, no-store"),("X-Robots-Tag","noindex, nofollow,noarchive"),("Referrer-Policy","no-referrer"),("X-Content-Type-Options","nosniff"),("Content-Security-Policy","default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")): self.send_header(name,value)
            if cookie: self.send_header("Set-Cookie", cookie)
            self.end_headers(); self.wfile.write(encoded)
        def do_GET(self):
            if urlparse(self.path).path != "/stage-b-demo/receipts": self.send_error(404); return
            morsel = SimpleCookie(self.headers.get("Cookie", "")).get("bb_demo_session")
            credentials = session_credentials(morsel.value) if morsel else None
            if credentials:
                try: self._reply(200, render_receipts(project_receipts(fetch_receipts(upstream_host, upstream_port, *credentials)))); return
                except (PermissionError, OSError, json.JSONDecodeError): pass
            self._reply(200, render_login())
        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/stage-b-demo/logout":
                morsel = SimpleCookie(self.headers.get("Cookie", "")).get("bb_demo_session")
                if morsel: sessions.pop(morsel.value.rsplit(".", 1)[0], None)
                self._reply(200, render_login(), "bb_demo_session=; Max-Age=0; Path=/stage-b-demo/; HttpOnly; Secure; SameSite=Strict"); return
            if path == "/stage-b-demo/answer":
                morsel = SimpleCookie(self.headers.get("Cookie", "")).get("bb_demo_session")
                identity = session_credentials(morsel.value) if morsel else None
                try:
                    length = int(self.headers.get("Content-Length", "0")); form = parse_qs(self.rfile.read(length).decode())
                    check_id, answer = form.get("check_id", [""])[-1], form.get("answer", [""])[-1]
                    if not identity or answer not in {"OK", "STOP"}: raise PermissionError
                    relay_result = submit_answer(upstream_host, upstream_port, *identity, check_id, answer)
                    rows = fetch_receipts(upstream_host, upstream_port, *identity)
                except (ValueError, PermissionError, OSError, json.JSONDecodeError): self._reply(400, _page('<p class="error" role="alert">The answer could not be saved.</p>')); return
                notice = '<p role="status">Answer saved. Email accepted for sending; agent acknowledgement has not been observed.</p>' if relay_result == "sent" else '<p class="error" role="status">Answer saved; it has not been sent to the agent. You can retry safely.</p>'
                self._reply(200, _page(notice + render_receipts(project_receipts(rows)).split("<main>", 1)[1].split("</main>", 1)[0])); return
            if path != "/stage-b-demo/receipts": self.send_error(404); return
            try:
                length = int(self.headers.get("Content-Length","0"))
                if length <= 0 or length > 8192: raise ValueError
                form = parse_qs(self.rfile.read(length).decode()); email, passphrase = form.get("email",[""])[-1], form.get("passphrase",[""])[-1]
                verified = credentials.verify(email, passphrase) if credentials else None
                if not verified: raise PermissionError
                token, generation = verified
                rows = fetch_receipts(upstream_host, upstream_port, email, token)
            except (ValueError, PermissionError, OSError, json.JSONDecodeError): self._reply(401, render_login("Email or passphrase was not accepted.")); return
            cookie = None
            if session_secret is not None:
                session_id = secrets.token_urlsafe(24)
                make_room(); sessions[session_id] = (int(time.time()) + session_ttl, email, token, generation)
                cookie = f"bb_demo_session={signed_session(session_id)}; Max-Age={session_ttl}; Path=/stage-b-demo/; HttpOnly; Secure; SameSite=Strict"
            self._reply(200, render_receipts(project_receipts(rows)), cookie)
        def log_message(self, *_): return
    return Handler

def build_server(host: str, port: int, upstream_host: str, upstream_port: int,
                 session_secret: str | None = None, session_ttl: int = 900,
                 credential_file: str | None = None, max_sessions: int = 256) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(upstream_host, upstream_port, session_secret, session_ttl, credential_file, max_sessions))

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--host",default="127.0.0.1"); parser.add_argument("--port",type=int,default=8789); parser.add_argument("--upstream-host",default="127.0.0.1"); parser.add_argument("--upstream-port",type=int,default=8788); parser.add_argument("--session-secret",default=os.environ.get("BB_DEMO_SESSION_SECRET")); parser.add_argument("--session-ttl",type=int,default=900); parser.add_argument("--max-sessions",type=int,default=256); parser.add_argument("--credential-file",default=os.environ.get("BB_DEMO_CREDENTIAL_FILE")); parser.add_argument("--provision",metavar="EMAIL"); parser.add_argument("--revoke",metavar="EMAIL"); args = parser.parse_args()
    if args.provision or args.revoke:
        if not args.credential_file: parser.error("--credential-file is required")
        store = CredentialStore(args.credential_file)
        if args.revoke: store.revoke(args.revoke); return
        first=getpass.getpass("Pilot passphrase: "); second=getpass.getpass("Repeat passphrase: ")
        if first != second: parser.error("passphrases do not match")
        store.provision(args.provision, first, getpass.getpass("Enrollment token: ")); return
    if not args.session_secret or len(args.session_secret) < 32:
        parser.error("BB_DEMO_SESSION_SECRET must be at least 32 characters")
    if not args.credential_file:
        parser.error("BB_DEMO_CREDENTIAL_FILE is required")
    build_server(args.host,args.port,args.upstream_host,args.upstream_port,args.session_secret,args.session_ttl,args.credential_file,args.max_sessions).serve_forever()
if __name__ == "__main__": main()
