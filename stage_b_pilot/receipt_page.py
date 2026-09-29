"""Private authenticated HTML view over persisted Stage B DEMO receipts."""
from __future__ import annotations
import argparse, hashlib, hmac, html, json, os, secrets, time
from dataclasses import dataclass
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote, urlparse

@dataclass(frozen=True)
class DemoReceipt:
    check_id: str; proposed_action: str; terms: str; deposit: str
    money_at_risk: str; user_answer: str; outcome: str; outcome_detail: str

def _structured_body(body: str) -> dict[str, str]:
    allowed = {"check id":"correlation_key", "backbond check id":"correlation_key", "correlation id":"correlation_key", "proposed action":"proposed_action", "terms":"terms", "deposit":"deposit", "money at risk":"money_at_risk", "your answer":"user_answer", "user answer":"user_answer"}
    values = {}
    for line in body.splitlines():
        key, separator, value = line.partition(":")
        field = allowed.get(key.strip().lower())
        if separator and field and value.strip(): values[field] = value.strip()[:500]
    return values

def project_receipts(rows: list[dict[str, object]]) -> list[DemoReceipt]:
    projected = []
    confirmations: dict[str, dict[str, object]] = {}
    for row in rows:
        if row.get("kind") != "forwarded_confirmation": continue
        fields = _structured_body(str(row.get("body_text", "")))
        key = str(row.get("correlation_key") or fields.get("correlation_key") or "")
        if key and key not in confirmations: confirmations[key] = row
    for row in (row for row in rows if row.get("kind") == "check"):
        fields = _structured_body(str(row.get("body_text", "")))
        key = str(row.get("correlation_key") or fields.get("correlation_key") or "")
        confirmation = confirmations.get(key) if key else None
        if confirmation is None:
            outcome, detail = "NO CONFIRMATION RECEIVED", "No independent confirmation has arrived for this check."
        else:
            confirmed = _structured_body(str(confirmation.get("body_text", "")))
            comparable = {key:value for key,value in fields.items() if key != "user_answer"}
            matches = bool(comparable) and all(confirmed.get(key) == value for key,value in comparable.items())
            outcome = "MATCH" if matches else "MISMATCH"
            detail = "The observed result matched the submitted check." if matches else "The observed result differed from the submitted check."
        projected.append(DemoReceipt(str(row.get("receipt_id", "Recorded check"))[:80], fields.get("proposed_action", "Not provided"), fields.get("terms", "Not provided"), fields.get("deposit", "Not provided"), fields.get("money_at_risk", "Not provided"), fields.get("user_answer", "Not provided"), outcome, detail))
    return projected

def _page(content: str) -> str:
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><meta name="referrer" content="no-referrer"><title>Private receipts | BackBond</title><style>
:root{{--ink:#0c2030;--paper:#f7f8f6;--copper:#9a4f24;--line:#cbd3d6;--muted:#52636c;--white:#fff;font-family:Inter,Arial,sans-serif;color:var(--ink);background:var(--paper);color-scheme:light}}*{{box-sizing:border-box}}body{{margin:0}}header{{min-height:68px;padding:1rem max(20px,calc((100vw - 980px)/2));display:flex;align-items:center;justify-content:space-between;gap:1rem;border-bottom:1px solid var(--line);background:var(--white)}}.brand{{font:800 1.25rem Archivo,Arial,sans-serif}}.demo{{border:2px solid var(--copper);padding:.4rem .7rem;font:800 .8rem 'IBM Plex Mono',monospace;letter-spacing:.08em}}main{{width:min(100% - 40px,980px);margin:0 auto;padding:clamp(2rem,6vw,5rem) 0}}.intro{{display:grid;grid-template-columns:minmax(0,1fr) 15rem;gap:3rem;align-items:end;border-bottom:3px solid var(--ink);padding-bottom:2rem}}h1{{font:800 clamp(2.25rem,7vw,5rem)/.98 Archivo,Arial,sans-serif;letter-spacing:-.045em;margin:0}}h2{{font:800 clamp(1.25rem,3vw,1.7rem) Archivo,Arial,sans-serif;margin:0}}p{{line-height:1.55}}.privacy,.muted{{color:var(--muted)}}.receipt{{padding:2rem 0;border-bottom:3px solid var(--ink)}}.outcome{{padding:1rem 0;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:1rem;align-items:center}}.outcome span{{color:var(--copper);font:800 .84rem 'IBM Plex Mono',monospace;text-align:right}}dl{{margin:0;display:grid;grid-template-columns:13rem 1fr}}dt,dd{{margin:0;padding:1.1rem 0;border-bottom:1px solid var(--line)}}dt{{color:var(--muted)}}dd{{font-weight:700;overflow-wrap:anywhere}}form{{max-width:34rem;margin-top:2rem}}label{{display:block;font-weight:700;margin-top:1rem}}input{{width:100%;margin-top:.45rem;border:1px solid #64737a;border-radius:4px;background:var(--white);color:var(--ink);font:inherit;padding:.8rem}}button{{margin-top:1.25rem;border:0;border-radius:4px;background:var(--ink);color:var(--white);font:800 1rem Archivo,Arial,sans-serif;padding:.85rem 1.1rem;cursor:pointer}}button:active{{transform:translateY(1px)}}.error{{border-left:4px solid var(--copper);padding:.75rem 1rem;background:var(--white)}}footer{{margin-top:2rem;color:var(--muted);font-size:.875rem}}:focus-visible{{outline:3px solid #2877ad;outline-offset:3px}}@media(max-width:640px){{header{{align-items:flex-start;flex-direction:column}}main{{width:min(100% - 32px,980px);padding:2rem 0}}.intro{{display:block}}.privacy{{margin-top:1rem}}.outcome{{align-items:flex-start;flex-direction:column}}.outcome span{{text-align:left}}dl{{grid-template-columns:1fr}}dt{{padding-bottom:.25rem;border-bottom:0}}dd{{padding-top:0}}}}</style></head><body><header><span class="brand">BackBond</span><span class="demo">DEMO PRIVATE</span></header><main>{content}<footer>DEMO data only. No payment or account connection.</footer></main></body></html>'''

def render_login(error: str = "") -> str:
    message = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return _page(f'''<div class="intro"><h1>Observation receipts</h1><p class="privacy">Sign in with the email and confirmation token used for this private pilot.</p></div>{message}<form method="post" action="/stage-b-demo/receipts" autocomplete="off"><label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email" required><label for="token">Confirmation token</label><input id="token" name="token" type="password" autocomplete="off" required><button type="submit">View receipts</button></form>''')

def render_receipts(receipts: list[DemoReceipt]) -> str:
    if not receipts: body = '<p class="muted" role="status">No checks have been received for this account yet.</p>'
    else:
        blocks = []
        for receipt in receipts:
            v = {name:html.escape(str(value)) for name,value in receipt.__dict__.items()}
            blocks.append(f'''<article class="receipt"><div class="outcome" role="status"><h2>{v['outcome_detail']}</h2><span>{v['outcome']}</span></div><dl><dt>Check</dt><dd>{v['check_id']}</dd><dt>Proposed action</dt><dd>{v['proposed_action']}</dd><dt>Terms</dt><dd>{v['terms']}</dd><dt>Deposit</dt><dd>{v['deposit']}</dd><dt>Money at risk</dt><dd>{v['money_at_risk']}</dd><dt>Your answer</dt><dd>{v['user_answer']}</dd><dt>Later outcome</dt><dd>{v['outcome']}</dd></dl></article>''')
        body = "".join(blocks)
    return _page(f'<div class="intro"><h1>Observation receipts</h1><p class="privacy">Private persisted records for this confirmed pilot account.</p></div>{body}')

def fetch_receipts(host: str, port: int, email: str, token: str) -> list[dict[str, object]]:
    connection = HTTPConnection(host, port, timeout=5)
    try:
        connection.request("GET", f"/receipts?email={quote(email)}", headers={"Authorization":f"Bearer {token}"})
        response = connection.getresponse(); payload = json.loads(response.read())
    finally: connection.close()
    if response.status != 200: raise PermissionError
    return payload.get("receipts", [])

def make_handler(upstream_host: str, upstream_port: int, session_secret: str | None = None,
                 session_ttl: int = 900) -> type[BaseHTTPRequestHandler]:
    if session_ttl < 1: raise ValueError("session TTL must be positive")
    sessions: dict[str, tuple[int, str, str]] = {}

    def signed_session(session_id: str) -> str:
        signature = hmac.new((session_secret or "").encode(), session_id.encode(), hashlib.sha256).hexdigest()
        return f"{session_id}.{signature}"

    def session_credentials(value: str) -> tuple[str, str] | None:
        if session_secret is None: return None
        try: session_id, supplied = value.rsplit(".", 1)
        except ValueError: return None
        if not hmac.compare_digest(signed_session(session_id).rsplit(".", 1)[1], supplied): return None
        record = sessions.get(session_id)
        if record is None or record[0] <= int(time.time()):
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
            if urlparse(self.path).path != "/stage-b-demo/receipts": self.send_error(404); return
            try:
                length = int(self.headers.get("Content-Length","0"))
                if length <= 0 or length > 8192: raise ValueError
                form = parse_qs(self.rfile.read(length).decode()); email, token = form.get("email",[""])[-1], form.get("token",[""])[-1]
                rows = fetch_receipts(upstream_host, upstream_port, email, token)
            except (ValueError, PermissionError, OSError, json.JSONDecodeError): self._reply(401, render_login("Email or confirmation token was not accepted.")); return
            cookie = None
            if session_secret is not None:
                session_id = secrets.token_urlsafe(24)
                sessions[session_id] = (int(time.time()) + session_ttl, email, token)
                cookie = f"bb_demo_session={signed_session(session_id)}; Max-Age={session_ttl}; Path=/stage-b-demo/; HttpOnly; Secure; SameSite=Strict"
            self._reply(200, render_receipts(project_receipts(rows)), cookie)
        def log_message(self, *_): return
    return Handler

def build_server(host: str, port: int, upstream_host: str, upstream_port: int,
                 session_secret: str | None = None, session_ttl: int = 900) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(upstream_host, upstream_port, session_secret, session_ttl))

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--host",default="127.0.0.1"); parser.add_argument("--port",type=int,default=8789); parser.add_argument("--upstream-host",default="127.0.0.1"); parser.add_argument("--upstream-port",type=int,default=8788); parser.add_argument("--session-secret",default=os.environ.get("BB_DEMO_SESSION_SECRET")); parser.add_argument("--session-ttl",type=int,default=900); args = parser.parse_args(); build_server(args.host,args.port,args.upstream_host,args.upstream_port,args.session_secret,args.session_ttl).serve_forever()
if __name__ == "__main__": main()
