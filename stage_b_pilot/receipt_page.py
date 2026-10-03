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
            if os.name != "nt" and os.stat(self.path).st_mode & 0o077:
                raise PermissionError("credential file must be mode 0600")
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
    correlation_reference: str = "Reference unavailable"
    limit_state: str = "limit not set"; obligation_status: str = "open"
    reported_window_start: str = "Not provided"
    reported_window_end: str = "Not provided"
    reported_at: str = "Not provided"

    @property
    def is_cancellation_obligation(self) -> bool:
        action = self.proposed_action.strip().lower()
        return action.startswith("cancel ") or action.startswith("cancellation ")

def _structured_body(body: str) -> dict[str, str]:
    allowed = {"check id":"correlation_key", "backbond check id":"correlation_key", "correlation id":"correlation_key", "proposed action":"proposed_action", "terms":"terms", "deposit":"deposit", "money at risk":"money_at_risk", "your answer":"user_answer", "user answer":"user_answer", "window start":"window_start", "window end":"window_end", "task limit":"task_limit", "obligation status":"obligation_status"}
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
        proposed_action = fields.get("proposed_action", "Not provided")
        cancellation_check = proposed_action.strip().lower().startswith("cancel ")
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
        answer = "No response required" if cancellation_check else fields.get("user_answer", "Not provided")
        limit = fields.get("task_limit", "limit not set")
        limit_state = "over your limit" if limit.lower() == "over" else "limit not set" if limit.lower() in {"", "not set"} else limit
        obligation = fields.get("obligation_status", "open").lower()
        if obligation not in {"open", "cancelled", "missed"}: obligation = "open"
        projected.append(DemoReceipt(str(row.get("receipt_id", "Recorded check"))[:80], proposed_action, fields.get("terms", "Not provided"), fields.get("deposit", "Not provided"), fields.get("money_at_risk", "Not provided"), answer, outcome, detail, str(key) if key else "Reference unavailable", limit_state, obligation, fields.get("window_start", "Not provided"), fields.get("window_end", "Not provided"), str(row.get("received_at") or "Not provided")))
    return projected

def _page(content: str) -> str:
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><meta name="referrer" content="no-referrer"><title>Private receipts | BackBond</title><style>
:root{{--ink:#0B1A38;--paper:#FCFBF8;--copper:#BD6A34;--line:#cbd3d6;--muted:#535E77;--white:#fff;font-family:Inter,Arial,sans-serif;color:var(--ink);background:var(--paper);color-scheme:light}}*{{box-sizing:border-box}}body{{margin:0}}header{{min-height:68px;padding:1rem max(20px,calc((100vw - 980px)/2));display:flex;align-items:center;justify-content:space-between;gap:1rem;border-bottom:1px solid var(--line);background:var(--white)}}.brand{{font:700 1rem Inter,Arial,sans-serif}}.demo{{border:2px solid var(--copper);padding:.4rem .7rem;font:800 .8rem 'IBM Plex Mono Medium',monospace;letter-spacing:.08em}}main{{width:min(100% - 40px,980px);margin:0 auto;padding:clamp(2rem,6vw,5rem) 0}}.intro{{display:grid;grid-template-columns:minmax(0,1fr) 15rem;gap:3rem;align-items:end;border-bottom:3px solid var(--ink);padding-bottom:2rem}}h1{{font:800 clamp(2.25rem,7vw,5rem)/.98 Archivo,Arial,sans-serif;letter-spacing:-.045em;margin:0}}h2{{font:800 clamp(1.25rem,3vw,1.7rem) Archivo,Arial,sans-serif;margin:0}}p{{line-height:1.55}}.privacy,.muted{{color:var(--muted)}}.receipt{{padding:2rem 0;border-bottom:3px solid var(--ink)}}.outcome{{padding:1rem 0;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:1rem;align-items:center}}.outcome span{{color:var(--copper);font:800 .84rem 'IBM Plex Mono Medium',monospace;text-align:right}}dl{{margin:0;display:grid;grid-template-columns:13rem 1fr}}dt,dd{{margin:0;padding:1.1rem 0;border-bottom:1px solid var(--line)}}dt{{color:var(--muted)}}dd{{font-weight:700;overflow-wrap:anywhere}}form{{max-width:34rem;margin-top:2rem}}label{{display:block;font-weight:700;margin-top:1rem}}input,select,textarea{{width:100%;margin-top:.45rem;border:1px solid #64737a;border-radius:4px;background:var(--white);color:var(--ink);font:inherit;padding:.8rem}}input[type=checkbox]{{width:auto;margin-right:.5rem}}textarea{{max-width:100%;overflow-wrap:anywhere}}nav{{margin-bottom:2rem}}details{{padding:1rem 0;border-bottom:1px solid var(--line)}}summary{{cursor:pointer;font-weight:700}}button{{margin-top:1.25rem;border:0;border-radius:4px;background:var(--ink);color:var(--white);font:800 1rem Archivo,Arial,sans-serif;padding:.85rem 1.1rem;cursor:pointer}}button:active{{transform:translateY(1px)}}.error{{border-left:4px solid var(--copper);padding:.75rem 1rem;background:var(--white)}}footer{{margin-top:2rem;color:var(--muted);font-size:.875rem}}:focus-visible{{outline:3px solid #2877ad;outline-offset:3px}}@media(max-width:640px){{header{{align-items:flex-start;flex-direction:column}}main{{width:min(100% - 32px,980px);padding:2rem 0}}.intro{{display:block}}.privacy{{margin-top:1rem}}.outcome{{align-items:flex-start;flex-direction:column}}.outcome span{{text-align:left}}dl{{grid-template-columns:1fr}}dt{{padding-bottom:.25rem;border-bottom:0}}dd{{padding-top:0}}}}</style></head><body><header><span class="brand">BackBond</span><span class="demo">DEMO PRIVATE</span></header><main>{content}<footer>Pilot only. No payment or insurance activation.</footer></main></body></html>'''

def render_login(error: str = "") -> str:
    message = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return _page(f'''<div class="intro"><h1>Observation receipts</h1><p class="privacy">Sign in with your private pilot passphrase.</p></div>{message}<form method="post" action="/stage-b-demo/receipts" autocomplete="off"><label for="email">Email</label><input id="email" name="email" type="email" autocomplete="email" required><label for="passphrase">Passphrase</label><input id="passphrase" name="passphrase" type="password" autocomplete="current-password" required><button type="submit">View receipts</button></form>''')

def render_receipts(receipts: list[DemoReceipt]) -> str:
    if not receipts: body = '<p class="muted" role="status">No checks have been received for this account yet.</p>'
    else:
        blocks = []
        for receipt in receipts:
            v = {name:html.escape(str(value)) for name,value in receipt.__dict__.items()}
            blocks.append(f'''<article class="receipt"><div class="outcome" role="status"><h2>{v['outcome_detail']}</h2><span>{v['outcome']}</span></div><dl><dt>Check ID / email reference</dt><dd>{v['correlation_reference']}</dd><dt>Internal receipt ID</dt><dd>{v['check_id']}</dd><dt>Proposed action</dt><dd>{v['proposed_action']}</dd><dt>Terms</dt><dd>{v['terms']}</dd><dt>Deposit</dt><dd>{v['deposit']}</dd><dt>Money at risk</dt><dd>{v['money_at_risk']}</dd><dt>Per-task limit</dt><dd>{v['limit_state']}</dd><dt>Reported by your agent</dt><dd>{v['user_answer']}</dd><dt>Agent-reported window start</dt><dd>{v['reported_window_start']}</dd><dt>Agent-reported window end</dt><dd>{v['reported_window_end']}</dd><dt>Agent report received</dt><dd>{v['reported_at']}</dd><dt>Obligation</dt><dd>{v['obligation_status']}</dd><dt>Later outcome</dt><dd>{v['outcome']}</dd></dl></article>''')
        body = "".join(blocks)
    return _page(f'<nav><a href="/stage-b-demo/history">Onboarding history</a> | <a href="/stage-b-demo/receipts">Ongoing task support</a></nav><div class="intro"><h1>Observation receipts</h1><p class="privacy">Private persisted records for this confirmed pilot account.</p></div><form method="post" action="/stage-b-demo/logout"><button type="submit">Log out</button></form>{body}')

def fetch_receipts(host: str, port: int, email: str, token: str) -> list[dict[str, object]]:
    connection = HTTPConnection(host, port, timeout=5)
    try:
        connection.request("GET", f"/receipts?email={quote(email)}", headers={"Authorization":f"Bearer {token}"})
        response = connection.getresponse(); payload = json.loads(response.read())
    finally: connection.close()
    if response.status != 200: raise PermissionError
    return payload.get("receipts", [])


def history_api(host: str, port: int, email: str, token: str,
                action: str | None = None, fields: dict | None = None) -> dict:
    path = f"/history/{action}" if action else f"/history/state?email={quote(email)}"
    connection = HTTPConnection(host, port, timeout=5)
    try:
        body = json.dumps({"email": email, **(fields or {})}).encode() if action else None
        headers = {"Authorization": f"Bearer {token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request("POST" if action else "GET", path, body=body, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
    finally:
        connection.close()
    if response.status != 200:
        raise ValueError(str(payload.get("error", "history_request_failed")))
    if action is None and not {"onboarding", "imports"}.issubset(payload):
        raise ValueError("history_response_invalid")
    return payload

def instinct_api(host: str, port: int, email: str, token: str,
                 action: str, fields: dict | None = None) -> dict:
    connection = HTTPConnection(host, port, timeout=5)
    try:
        body = json.dumps({"email": email, **(fields or {})}).encode()
        connection.request("POST", f"/instinct/{action}", body=body, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        response = connection.getresponse(); payload = json.loads(response.read())
    finally: connection.close()
    if response.status != 200:
        raise ValueError(str(payload.get("error", "instinct_request_failed")))
    return payload

def optional_instinct_reports(host: str, port: int, email: str, token: str) -> dict:
    try:
        return instinct_api(host, port, email, token, "reports")
    except (ValueError, OSError, json.JSONDecodeError):
        return {"reports": []}


def history_instruction(address: str, email: str, days: int, *, synthetic: bool) -> str:
    task_kind = "fictional test tasks" if synthetic else "your Instinct task records"
    example_row = {key: "unknown" for key in (
        "occurred_at", "timezone", "task_id", "category", "proposed_action",
        "external_action", "approval_status", "approval_source", "action_taken",
        "outcome", "amount", "currency", "recovery", "evidence_origin", "gaps",
    )}
    example_row["evidence_origin"] = "agent_reported"
    example_row["synthetic"] = synthetic
    example = json.dumps({"schema_version": "instinct-history/v1", "row_count": 1,
                          "rows": [example_row], "end_marker": "END INSTINCT HISTORY"})
    return (
        f"Send one email from {email} to {address} with subject 'BackBond Instinct history'. "
        f"Use only {task_kind} from the last {days} days. Do not use connected email, "
        "calendar, files, credentials, card details, health data, raw chats, or third-party "
        "personal information. Reply in chat without emailing if there are no tasks. "
        "The email's plain-text body must be one JSON object with schema_version "
        "'instinct-history/v1', row_count equal to the number of rows, rows, and "
        "end_marker 'END INSTINCT HISTORY'. Include between 1 and 500 rows. "
        "Each row must contain occurred_at (ISO 8601 date or timestamp), timezone, task_id, category, "
        "proposed_action, external_action, approval_status, approval_source, "
        "action_taken, outcome, amount, currency, recovery, evidence_origin, gaps, "
        f"and synthetic. Use 'unknown' for unavailable facts. Set evidence_origin "
        f"to 'agent_reported' and synthetic to {'true' if synthetic else 'false'} "
        "for every row. Do not invent an approval, charge, loss, or result. "
        "No attachments, CC, or BCC. Use this exact JSON structure, replacing "
        f"the example row with the actual scoped rows: {example}"
    )


def render_history(payload: dict, email: str, csrf: str, *, issued: dict | None = None,
                   standing: dict | None = None, standing_issued: dict | None = None,
                   error: str = "") -> str:
    escape = lambda value: html.escape(str(value), quote=True)
    onboarding = payload.get("onboarding", {})
    support = payload.get("support", {})
    imports = payload.get("imports", [])
    external = payload.get("external_enabled") is True
    synthetic_enabled = payload.get("synthetic_enabled") is True
    notice = f'<p class="error" role="alert">{escape(error)}</p>' if error else ""
    form = ""
    if external or synthetic_enabled:
        synthetic_field = ('<input type="hidden" name="synthetic" value="yes">'
                           if not external else "")
        label = "Create synthetic test request" if not external else "Create history request"
        form = f'''<form method="post" action="/stage-b-demo/history/request">
<input type="hidden" name="csrf" value="{escape(csrf)}">{synthetic_field}
<label for="purpose">Use</label><select id="purpose" name="purpose"><option value="onboarding">New user onboarding</option><option value="support">Ongoing support update</option></select>
<label for="lookback">Lookback</label><select id="lookback" name="lookback_days"><option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option></select>
<label><input type="checkbox" name="consent" value="yes" required> I authorize BackBond to receive this selected Instinct task history for the purpose chosen above and make it available for underwriting review or ongoing support, as applicable. Instinct's report is agent-reported, not proof of a charge, loss, or approval. I can view or delete it. This does not start insurance or coverage.</label>
<button type="submit">{label}</button></form>'''
    else:
        form = '<p class="muted">History requests are not enabled for this account yet.</p>'
    instruction = ""
    if issued:
        days = int(issued.get("lookback_days", 7))
        instruction_text = history_instruction(str(issued["address"]), str(issued["sender_email"]), days,
                                               synthetic=bool(issued.get("synthetic")))
        instruction = f'''<section class="receipt"><h2>Give Instinct this instruction</h2>
<p>The one-time address expires at {escape(issued['expires_at'])}. A valid report is received without another step from you. You can view or delete it here.</p>
<textarea readonly rows="11">{escape(instruction_text)}</textarea></section>'''
    standing_reports = (standing or {}).get("reports", [])
    standing_grants = (standing or {}).get("grants", [])
    active_grants = [grant for grant in standing_grants if grant.get("state") == "active"]
    enrolled_sender = (standing or {}).get("enrolled_sender", "Not enrolled")
    standing_section = f'''<section class="receipt"><h2>One-link Instinct setup</h2>
<p>Create one short account-bound link. It contains the separate one-time multi-row onboarding address and the standing per-request address. Delivery status appears here; Instinct cannot see Mailgun webhook responses.</p>
<p>This fictional pilot requests up to 90 days of onboarding task history. The one-time history address expires after one hour. The standing report address accepts each later fictional request for 24 hours, or until you revoke it. Only the enrolled agent sender, {escape(enrolled_sender)}, is accepted. Reports contain the time, category, proposed and actual external action, approval source and status, money at risk, outcome, evidence origin, and gaps. They remain agent-reported for staff review and do not activate insurance or coverage.</p>
<form method="post" action="/stage-b-demo/instinct/grant"><input type="hidden" name="csrf" value="{escape(csrf)}"><label><input type="checkbox" name="consent" value="yes" required> I authorize this scoped fictional history and per-request reporting pilot.</label><button type="submit">Create short link</button></form>'''
    if standing_issued:
        standing_section += f'''<p role="status">Short link ready: <a rel="noreferrer" href="{escape(standing_issued['instruction_url'])}">{escape(standing_issued['instruction_url'])}</a></p><p>Give Instinct only this link and the scoped authorization above. The link is shown once. One-time onboarding address: {escape(standing_issued['history_address'])}</p>'''
    if active_grants:
        standing_section += "<h3>Active links</h3>" + "".join(
            f'''<p>Created {escape(grant['created_at'])}; expires {escape(grant['expires_at'])}; sender {escape(grant['sender_email'])}.</p><form method="post" action="/stage-b-demo/instinct/revoke"><input type="hidden" name="csrf" value="{escape(csrf)}"><input type="hidden" name="grant_id" value="{escape(grant['grant_id'])}"><button type="submit">Revoke this link</button></form>'''
            for grant in active_grants)
    else:
        standing_section += '<p class="muted" role="status">No active one-link instruction.</p>'
    if standing_reports:
        standing_section += "<h2>Per-request report status</h2>" + "".join(
            f"<p role='status'>{escape(row['report_id'])}: received; {escape(row['review'])}; source {escape(row['provenance'])}.</p>"
            for row in standing_reports)
    else:
        standing_section += '<p class="muted" role="status">No per-request report has been received for this account yet.</p>'
    standing_section += "</section>"
    blocks = []
    for imported in imports:
        preview_id = escape(imported["preview_id"])
        report = imported.get("report", {})
        rows = report.get("rows", [])
        entries = []
        for index, row in enumerate(rows):
            details = "".join(
                f"<dt>{escape(key.replace('_', ' ').title())}</dt><dd>{escape(value)}</dd>"
                for key, value in row.items() if key != "user_corrections"
            )
            corrections = row.get("user_corrections", {})
            if corrections:
                details += "<dt>Your corrections</dt><dd>" + escape(corrections) + "</dd>"
            correction_form = ""
            if imported["state"] == "pending_confirmation":
                options = "".join(
                    f'<option value="{escape(key)}">{escape(key.replace("_", " ").title())}</option>'
                    for key in ("task_id", "approval_status", "approval_source", "action_taken", "outcome", "amount", "currency", "gaps")
                )
                correction_form = f'''<form method="post" action="/stage-b-demo/history/action">
<input type="hidden" name="csrf" value="{escape(csrf)}"><input type="hidden" name="action" value="correct_field">
<input type="hidden" name="preview_id" value="{preview_id}"><input type="hidden" name="row_index" value="{index}">
<label>Correct a field <select name="field">{options}</select></label><input name="value" maxlength="500" required><button type="submit">Save correction</button></form>'''
            entries.append(f'<details><summary>Task {index + 1}: {escape(row.get("category", "unknown"))}</summary><dl>{details}</dl>{correction_form}</details>')
        actions = ""
        if imported["state"] == "pending_confirmation":
            for action, label in (("confirm", "Confirm selected history"), ("discard", "Discard")):
                actions += f'''<form method="post" action="/stage-b-demo/history/action"><input type="hidden" name="csrf" value="{escape(csrf)}"><input type="hidden" name="preview_id" value="{preview_id}"><input type="hidden" name="action" value="{action}"><button type="submit">{label}</button></form>'''
        actions += f'''<form method="post" action="/stage-b-demo/history/action"><input type="hidden" name="csrf" value="{escape(csrf)}"><input type="hidden" name="preview_id" value="{preview_id}"><input type="hidden" name="action" value="delete"><button type="submit">Delete import</button></form>'''
        blocks.append(f'''<section class="receipt"><h2>{escape(imported['purpose'].title())} report</h2>
<p>Received {escape(imported['created_at'])}; {len(rows)} rows; {escape(imported['state'])}. Source: Instinct, agent-reported. Any earlier user corrections appear separately. No charge, loss, approval, or coverage is verified by this report alone.</p>
{''.join(entries)}{actions}</section>''')
    return _page(f'''<nav><a href="/stage-b-demo/history">Onboarding history</a> | <a href="/stage-b-demo/receipts">Ongoing task support</a></nav>
<div class="intro"><h1>Instinct history</h1><p class="privacy">Select a period and send the instruction to Instinct. BackBond receives a valid report without another step from you.</p></div>
{notice}<p>Onboarding evidence: {escape(onboarding.get('underwriting_evidence', 'incomplete'))}. Support update: {escape(support.get('history_state', 'history_not_supplied'))}.</p>
{standing_section}{form}{instruction}{''.join(blocks)}<form method="post" action="/stage-b-demo/logout"><button type="submit">Log out</button></form>''')

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

    def csrf_for(value: str) -> str:
        return hmac.new((session_secret or "").encode(),
                        (value + ":history").encode(), hashlib.sha256).hexdigest()

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: str, cookie: str | None = None) -> None:
            encoded = body.encode(); self.send_response(status)
            for name,value in (("Content-Type","text/html; charset=utf-8"),("Content-Length",str(len(encoded))),("Cache-Control","private, no-store"),("X-Robots-Tag","noindex, nofollow,noarchive"),("Referrer-Policy","no-referrer"),("X-Content-Type-Options","nosniff"),("Content-Security-Policy","default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")): self.send_header(name,value)
            if cookie: self.send_header("Set-Cookie", cookie)
            self.end_headers(); self.wfile.write(encoded)
        def do_GET(self):
            path = urlparse(self.path).path
            if path not in {"/stage-b-demo/receipts", "/stage-b-demo/history"}:
                self.send_error(404); return
            morsel = SimpleCookie(self.headers.get("Cookie", "")).get("bb_demo_session")
            credentials = session_credentials(morsel.value) if morsel else None
            if credentials:
                try:
                    if path == "/stage-b-demo/history":
                        payload = history_api(upstream_host, upstream_port, *credentials)
                        standing = optional_instinct_reports(upstream_host, upstream_port, *credentials)
                        self._reply(200, render_history(payload, credentials[0], csrf_for(morsel.value), standing=standing))
                    else:
                        self._reply(200, render_receipts(project_receipts(fetch_receipts(upstream_host, upstream_port, *credentials))))
                    return
                except (PermissionError, ValueError, OSError, json.JSONDecodeError):
                    self._reply(503, _page('<p role="alert">Private data is temporarily unavailable. Please retry.</p>'))
                    return
            self._reply(200, render_login())
        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/stage-b-demo/logout":
                morsel = SimpleCookie(self.headers.get("Cookie", "")).get("bb_demo_session")
                if morsel: sessions.pop(morsel.value.rsplit(".", 1)[0], None)
                self._reply(200, render_login(), "bb_demo_session=; Max-Age=0; Path=/stage-b-demo/; HttpOnly; Secure; SameSite=Strict"); return
            if path in {"/stage-b-demo/history/request", "/stage-b-demo/history/action",
                        "/stage-b-demo/instinct/grant", "/stage-b-demo/instinct/revoke"}:
                morsel = SimpleCookie(self.headers.get("Cookie", "")).get("bb_demo_session")
                active_user = session_credentials(morsel.value) if morsel else None
                if not active_user:
                    self._reply(401, render_login("Please sign in again.")); return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 8192: raise ValueError("request_size_invalid")
                    form = {key: values[-1] for key, values in parse_qs(self.rfile.read(length).decode()).items() if values}
                    if not hmac.compare_digest(form.get("csrf", ""), csrf_for(morsel.value)):
                        raise PermissionError("request_token_invalid")
                    standing_issued = None
                    if path == "/stage-b-demo/instinct/grant":
                        standing_issued = instinct_api(upstream_host, upstream_port, *active_user,
                                                       "grant", {"consent": form.get("consent") == "yes",
                                                                 "synthetic": True})
                        issued = None
                    elif path == "/stage-b-demo/instinct/revoke":
                        instinct_api(upstream_host, upstream_port, *active_user, "revoke",
                                     {"grant_id": form.get("grant_id", "")})
                        issued = None
                    elif path.endswith("/request"):
                        fields = {"purpose": form.get("purpose", "onboarding"),
                                  "lookback_days": int(form.get("lookback_days", "0")),
                                  "consent": form.get("consent") == "yes",
                                  "synthetic": form.get("synthetic") == "yes"}
                        issued = history_api(upstream_host, upstream_port, *active_user,
                                             action="request", fields=fields)
                        issued["lookback_days"] = fields["lookback_days"]
                    else:
                        fields = {"preview_id": form.get("preview_id", ""),
                                  "action": form.get("action", "")}
                        if fields["action"] == "correct_field":
                            fields.update(row_index=int(form.get("row_index", "-1")),
                                          field=form.get("field", ""), value=form.get("value", ""))
                        history_api(upstream_host, upstream_port, *active_user,
                                    action="action", fields=fields)
                        issued = None
                    payload = history_api(upstream_host, upstream_port, *active_user)
                    standing = optional_instinct_reports(upstream_host, upstream_port, *active_user)
                    self._reply(200, render_history(payload, active_user[0], csrf_for(morsel.value), issued=issued, standing=standing, standing_issued=standing_issued))
                except (ValueError, PermissionError, OSError, json.JSONDecodeError) as exc:
                    try:
                        payload = history_api(upstream_host, upstream_port, *active_user)
                    except (ValueError, OSError, json.JSONDecodeError):
                        payload = {}
                    self._reply(400, render_history(payload, active_user[0], csrf_for(morsel.value),
                                                     error=str(exc)))
                return
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
            try:
                payload = history_api(upstream_host, upstream_port, email, token)
                content = render_history(payload, email, csrf_for(signed_session(session_id))) if cookie else render_receipts(project_receipts(rows))
            except (ValueError, OSError, json.JSONDecodeError):
                content = render_receipts(project_receipts(rows))
            self._reply(200, content, cookie)
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
