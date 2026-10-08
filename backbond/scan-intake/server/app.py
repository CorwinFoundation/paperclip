"""FastAPI surface for the private partner QA intake service."""
from __future__ import annotations

import json
import os
import re
import threading
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from qa_runtime import DispatchNotSent, DispatchUnknown, QueueFull, ScanQueue, deliver_pending

from qa_service import (Code, ConnectionProofs, IntakeError, IntakeStore, RateGate,
                        scanner_payload, translate_callback, translate_tests, verify_webhook)

DB_PATH = os.environ.get("QA_DB_PATH", "/var/lib/backbond-qa/qa.sqlite")
SCANNER_URL = os.environ.get("QA_SCANNER_URL", "https://scanner.backbond.ai/scanner/v1")
PUBLIC_API = os.environ.get("QA_PUBLIC_API", "https://api.backbond.ai")
WEBHOOK_SECRET = os.environ.get("QA_WEBHOOK_SECRET", "")
PUBLIC_CODE = "PUBLIC"
PUBLIC_PARTNER = "public"
# are-scanner api/scan_handlers.py: pack starter-100@1 (100 messages) and one
# CONNECTIVITY_PROMPT ("Reply with exactly OK.") per connection check.
SCAN_MESSAGES = 100
CONNECTION_CHECK_MESSAGES = 1
PROOF_MESSAGE = "Your connection details changed or the connection test expired. Test the connection again."

@asynccontextmanager
async def lifespan(app):
    stopped = threading.Event()
    def worker():
        while not stopped.is_set():
            deliver_pending(store, _send_report)
            queue.expire_pending()
            stopped.wait(10)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        queue.close()
        thread.join(timeout=1)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["https://backbond.ai"], allow_credentials=False,
                   allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type"])
store = IntakeStore(DB_PATH)
proofs = ConnectionProofs(ttl=int(os.environ.get('QA_CONNECTION_PROOF_TTL', '900')))
check_gate = RateGate(email=(int(os.environ.get('QA_CHECKS_PER_EMAIL_HOUR', '10')), 3600),
                      ip=(int(os.environ.get('QA_CHECKS_PER_IP_HOUR', '30')), 3600),
                      all=(int(os.environ.get('QA_CHECKS_PER_HOUR', '300')), 3600))
public_gate = RateGate(ip=(int(os.environ.get('QA_PUBLIC_SCANS_PER_IP_DAY', '3')), 86400))


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    # Pydantic errors can contain the complete secret-bearing input. Do not
    # serialize exc.errors(), exc.body, input or validation context.
    return JSONResponse(status_code=422, content={"error": "Check the submitted fields and required consent."})


@app.exception_handler(HTTPException)
async def request_error(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})


@app.middleware("http")
async def private_response(request: Request, call_next):
    try:
        response = await call_next(request)
    except Exception:
        # Never put endpoints, keys or submitted prompts in logs or responses.
        response = JSONResponse(status_code=500, content={'error': 'QA service unavailable'})
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


class Auth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(pattern="^(bearer|header|none)$")
    header: str | None = None
    value: str | None = None

    @model_validator(mode="after")
    def valid(self):
        if self.type in {"bearer", "header"} and not self.value:
            raise ValueError("auth value is required")
        if self.type == "header" and not self.header:
            raise ValueError("auth header is required")
        return self


class ConnectionFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(default="", max_length=128)
    email: str = Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", max_length=254)
    agent_label: str = Field(min_length=1, max_length=120)
    endpoint_url: str = Field(max_length=2048)
    transport: str = Field(pattern="^(rest|openai_chat|json_map|vapi)$")
    auth: Auth
    model: str | None = None
    system_prompt: str | None = Field(default=None, max_length=32000)
    request_template: str | None = Field(default=None, max_length=32000)
    response_path: str | None = None
    vapi_assistant_id: str | None = None

    @model_validator(mode="after")
    def consent_and_transport(self):
        endpoint_host = (urlsplit(self.endpoint_url).hostname or "").lower().rstrip(".")
        if endpoint_host in {
            "backbond.ai", "www.backbond.ai", "scanner.backbond.ai", "api.backbond.ai",
        }:
            raise ValueError("endpoint_url must not target a BackBond scanner service")
        if self.transport == "openai_chat" and not self.model:
            raise ValueError("model is required for openai_chat")
        if self.transport == "json_map":
            template = json.loads(self.request_template or 'null')
            def markers(value):
                if isinstance(value, dict):
                    return sum(markers(child) for child in value.values())
                if isinstance(value, list):
                    return sum(markers(child) for child in value)
                if isinstance(value, str) and '{{prompt}}' in value:
                    if value != '{{prompt}}':
                        raise ValueError('prompt marker must be a complete value')
                    return 1
                return 0
            if not isinstance(template, dict) or markers(template) != 1:
                raise ValueError('request_template must contain one complete prompt value')
            if not self.response_path:
                raise ValueError("response_path is required for json_map")
        return self


class ScanRequest(ConnectionFields):
    consent_owner: bool
    consent_terms: bool
    terms_version: str = Field(default="", max_length=64)
    consent_improvement: bool = False  # Copies only de-identified test/result fields to the improvement store.
    connection_proof: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def consent(self):
        if not self.consent_owner or not self.consent_terms:
            raise ValueError("both consent fields are required")
        return self


def _tenant_tokens() -> dict:
    mapping = json.loads(os.environ.get('QA_SCANNER_TOKENS_JSON', '{}'))
    directory = os.environ.get('CREDENTIALS_DIRECTORY')
    if directory and (Path(directory) / 'qa-scanner-tokens.json').is_file():
        mapping = json.loads((Path(directory) / 'qa-scanner-tokens.json').read_text())
    return mapping


def _headers(data: dict | None = None) -> dict[str, str]:
    token = os.environ.get("QA_SCANNER_TOKEN", "")
    mapping = json.loads(os.environ.get('QA_SCANNER_TOKENS_JSON', '{}'))
    directory = os.environ.get('CREDENTIALS_DIRECTORY')
    if directory and (Path(directory) / 'qa-scanner-tokens.json').is_file():
        mapping = json.loads((Path(directory) / 'qa-scanner-tokens.json').read_text())
    if data and data.get('tenant_id') in mapping:
        token = mapping[data['tenant_id']]
    return {"Authorization": f"Bearer {token}"} if token else {}


def _scanner_check(data: dict) -> tuple[bool, str | None]:
    with httpx.Client(timeout=35) as client:
        response = client.post(f"{SCANNER_URL}/connectivity-check", json=data, headers=_headers(data))
    if response.status_code >= 400:
        return False, f"HTTP {response.status_code}"
    body = response.json()
    error = body.get("error")
    if not isinstance(error, str) or not (error in {'timeout', 'redirect refused', 'connection failed'} or re.fullmatch(r'HTTP [1-5][0-9]{2}', error)):
        error = 'Connection check failed. Check the endpoint, authentication and response settings.'
    return bool(body.get("success")), error


def _scanner_start(data: dict) -> dict:
    """Raise DispatchNotSent only when the scanner provably did not start the scan.
    Read/write timeouts, 5xx and unreadable 2xx bodies are ambiguous and propagate."""
    try:
        with httpx.Client(timeout=20) as client:
            response = client.post(f"{SCANNER_URL}/scans", json=data, headers=_headers(data))
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise DispatchNotSent() from None
    if 400 <= response.status_code < 500:
        raise DispatchNotSent()
    response.raise_for_status()
    return response.json()


def _mail_credentials_path() -> Path | None:
    configured = os.environ.get("SCAN_MAIL_CREDENTIALS_PATH")
    if configured:
        return Path(configured)
    credentials_directory = os.environ.get("CREDENTIALS_DIRECTORY")
    return Path(credentials_directory) / "scan-mail.json" if credentials_directory else None


def _mail_configured() -> bool:
    path = _mail_credentials_path()
    try:
        value = json.loads(path.read_text()) if path else {}
        return bool(value.get('provider') == 'resend' and value.get('api_key'))
    except (OSError, ValueError):
        return False


def _send_report(to: str, agent: str, token: str) -> None:
    path = _mail_credentials_path()
    if not path or not path.is_file():
        raise RuntimeError("Report mail is not configured")
    value = json.loads(path.read_text())
    if value.get('provider') != 'resend':
        raise RuntimeError('Unexpected mail provider')
    with httpx.Client(timeout=30) as client:
        response = client.post('https://api.resend.com/emails',
            headers={'Authorization': 'Bearer ' + value['api_key']}, json={
                'from': 'BackBond <info@backbond.ai>', 'to': [to],
                'subject': f'Your BackBond scan report — {agent}',
                'text': f'Your scan is complete.\n\nView the report:\nhttps://backbond.ai/scan/r/{token}'})
        if response.status_code not in {200, 201}:
            raise RuntimeError('Report mail delivery unavailable')


def _send_report_code(to: str, code: str) -> None:
    path = _mail_credentials_path()
    if not path or not path.is_file():
        raise RuntimeError("Report mail is not configured")
    value = json.loads(path.read_text())
    with httpx.Client(timeout=30) as client:
        response = client.post('https://api.resend.com/emails',
            headers={'Authorization': 'Bearer ' + value['api_key']}, json={
                'from': 'BackBond <info@backbond.ai>', 'to': [to],
                'subject': 'Your BackBond report verification code',
                'text': f'Your verification code is {code}. It expires in 10 minutes.'})
        if response.status_code not in {200, 201}:
            raise RuntimeError('Report mail delivery unavailable')


queue = ScanQueue(store, lambda payload: _scanner_start(payload),
                   pending_ttl=int(os.environ.get('QA_QUEUE_TTL_SECONDS', '900')))


class CodeRequest(BaseModel):
    code: str = Field(max_length=128)


@app.post("/scan/v1/check-code")
async def check_code(body: CodeRequest):
    return store.check_code(body.code)


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default) == '1'


def _terms_version() -> str:
    return os.environ.get('QA_TERMS_VERSION', 'v0.2.2')


def _private_acceptance(request: Request, code: str, email: str) -> bool:
    acceptance_token = os.environ.get('QA_ACCEPTANCE_TOKEN', '')
    return bool(acceptance_token and request.client and
        request.client.host in {'127.0.0.1', '::1'} and
        secrets.compare_digest(request.headers.get('x-qa-acceptance', ''), acceptance_token) and
        code == 'QA-ACCEPT-20261006' and email.lower() == 'ari@backbond.ai')


def _client(request: Request) -> str:
    return request.client.host if request.client else 'unknown'


def _resolve_code(code: str) -> dict:
    """Partner-code gate, default ON. OFF admits blank codes into the capped public tenant."""
    if code == PUBLIC_CODE:
        raise HTTPException(400, "invalid or expired code")
    if not code and not _flag('QA_PARTNER_CODE_REQUIRED', '1'):
        if not _tenant_tokens().get('partner-' + PUBLIC_PARTNER):
            # Public mode never falls back to the shared scanner token.
            raise HTTPException(503, "QA scans are temporarily unavailable")
        store.put_code(Code(PUBLIC_CODE, PUBLIC_PARTNER, "BackBond", "standard",
                            int(os.environ.get('QA_PUBLIC_MAX_EMAILS', '1000000')),
                            int(os.environ.get('QA_PUBLIC_SCANS_PER_EMAIL', '1')), "9999-12-31"))
        return {"code": PUBLIC_CODE, "partner": PUBLIC_PARTNER, "public": True}
    if not code or not store.check_code(code)["valid"]:
        raise HTTPException(400, "invalid or expired code")
    row = store.db.execute("SELECT partner FROM codes WHERE code=?", (code,)).fetchone()
    return {"code": code, "partner": row["partner"], "public": False}


def _public_caps(request: Request) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    if store.scans_since(PUBLIC_PARTNER, today) >= int(os.environ.get('QA_PUBLIC_DAILY_SCANS', '50')):
        raise HTTPException(429, "Free scans are at today's limit. Try again tomorrow.")
    if not public_gate.allow(ip=_client(request)):
        raise HTTPException(429, "This network has reached today's free scan limit.")


def _payload(clean: dict) -> dict:
    return scanner_payload(clean, callback_url=f"{PUBLIC_API}/scan/v1/scanner-webhook",
                           callback_secret_ref="partner-qa-v1")


@app.get("/scan/v1/config")
def config():
    return {"partner_code_required": _flag('QA_PARTNER_CODE_REQUIRED', '1'),
            "terms_version": _terms_version(),
            "scan_start_enabled": _flag("QA_TERMS_APPROVED", "0"),
            "connection_check_messages": CONNECTION_CHECK_MESSAGES,
            "scan_messages": SCAN_MESSAGES,
            "connection_proof_ttl_seconds": proofs.ttl}


@app.post("/scan/v1/connection-checks")
def connection_check(body: ConnectionFields, request: Request):
    """Connectivity only: one message, no reservation, no quota, cannot start a scan."""
    if os.environ.get('QA_READY') != '1' and not _private_acceptance(request, body.code, body.email):
        raise HTTPException(503, "QA scans are temporarily unavailable")
    resolved = _resolve_code(body.code)
    if not check_gate.allow(email=body.email.strip().lower(), ip=_client(request), all='all'):
        raise HTTPException(429, "Too many connection tests. Wait a few minutes and try again.")
    fields = body.model_dump()
    clean = dict(fields, code=resolved["code"], partner=resolved["partner"])
    payload = _payload(clean)
    try:
        ok, error = _scanner_check(payload)
    except Exception:
        fields.clear()
        raise HTTPException(502, "scanner unavailable")
    finally:
        payload.clear()
        clean.clear()
    try:
        if not ok:
            return {"ok": False, "error": error or "connection failed"}
        return {"ok": True, "connection_proof": proofs.issue(fields), "expires_in": proofs.ttl}
    finally:
        fields.clear()


@app.post("/scan/v1/scans")
def start_scan(body: ScanRequest, request: Request):
    if not _flag("QA_TERMS_APPROVED", "0"):
        raise HTTPException(503, "This review preview cannot start scans. Terms and launch approval are pending.")
    private_acceptance = _private_acceptance(request, body.code, body.email)
    if not _mail_configured() or not WEBHOOK_SECRET or (os.environ.get('QA_READY') != '1' and not private_acceptance):
        raise HTTPException(503, "QA scans are temporarily unavailable")
    # Legacy mode keeps the prior API (no proof, no terms version) and rechecks before launch.
    legacy = not _flag('QA_REQUIRE_CONNECTION_PROOF', '1') and body.connection_proof is None
    if body.terms_version != _terms_version() and not (legacy and not body.terms_version):
        raise HTTPException(400, "The Free Scan Terms have been updated. Reload the page and review them.")
    resolved = _resolve_code(body.code)
    if resolved["public"]:
        _public_caps(request)
    clean = body.model_dump()
    proof = clean.pop("connection_proof")
    if not legacy and not proofs.redeem(proof, clean):
        raise HTTPException(409, PROOF_MESSAGE)
    clean["code"], clean["partner"] = resolved["code"], resolved["partner"]
    try:
        reservation = store.reserve(code=clean["code"], email=str(clean["email"]),
            agent_label=clean["agent_label"], endpoint_url=clean["endpoint_url"],
            transport=clean["transport"], terms_version=body.terms_version or "unversioned-legacy",
            improvement_consent=body.consent_improvement)
    except IntakeError as exc:
        raise HTTPException(429 if "limit" in str(exc) or "used" in str(exc) else 400, str(exc))
    payload = _payload(clean)
    connection_passed = not legacy
    try:
        if legacy:
            ok, error = _scanner_check(payload)
            if not ok:
                store.connection_failed(reservation["scan_id"])
                raise HTTPException(400, error or "connection failed")
            connection_passed = True
        try:
            status = queue.submit(reservation['scan_id'], payload)
        except QueueFull:
            store.cancel_unsent(reservation['scan_id'])
            raise HTTPException(429, "BackBond is running the maximum number of scans. Try again in a few minutes.")
        payload = {}  # Ownership of any queued credential moves to the queue.
        return {"status": status, "status_token": reservation["status_token"]}
    except HTTPException:
        raise
    except DispatchUnknown:
        # The queue persisted dispatch_unknown and kept the slot; never report "not sent".
        payload = {}
        return {"status": "dispatch_unknown", "status_token": reservation["status_token"]}
    except DispatchNotSent:
        payload = {}  # The queue already recorded launch_failed and released the slot.
        raise HTTPException(502, "scanner unavailable")
    except Exception:
        if connection_passed:
            store.launch_failed(reservation["scan_id"])
        else:
            store.connection_failed(reservation["scan_id"])
        raise HTTPException(502, "scanner unavailable")
    finally:
        clean.pop("system_prompt", None)
        clean["auth"] = None
        payload.clear()


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status_token: str = Field(min_length=20, max_length=64)


def _status(token: str) -> dict:
    value = store.status(token)
    if not value:
        raise HTTPException(404, "scan not found")
    return value


@app.post("/scan/v1/scan-status")
def scan_status(body: StatusRequest):
    # POST so the bearer-style token never lands in access-log URLs.
    value = _status(body.status_token)
    value.pop("scan_id")
    return value


@app.post("/scan/v1/scan-cancel")
def scan_cancel(body: StatusRequest):
    value = _status(body.status_token)
    if value["status"] == "queued" and queue.cancel(value["scan_id"]):
        return {"status": "cancelled"}
    if value["status"] == "dispatch_unknown":
        raise HTTPException(409, "We could not confirm whether this scan started. It may still be sending "
                                 "test messages to your agent and cannot be stopped from here. "
                                 "Contact support@backbond.ai.")
    if value["status"] in {"reserved", "starting", "running"}:
        # The scanner exposes no remote cancel; do not claim one.
        raise HTTPException(409, "This scan has already been sent to the scanner and cannot be stopped "
                                 "from here. Contact support@backbond.ai.")
    raise HTTPException(409, "This scan is no longer active.")


@app.post("/scan/v1/scanner-webhook", status_code=204)
async def scanner_webhook(request: Request, x_backbond_signature: str = Header(default="")):
    if int(request.headers.get('content-length', '0')) > 1048576:
        raise HTTPException(413, 'callback too large')
    raw = await request.body()
    if len(raw) > 1048576:
        raise HTTPException(413, 'callback too large')
    if not WEBHOOK_SECRET or not verify_webhook(raw, x_backbond_signature, WEBHOOK_SECRET):
        raise HTTPException(401, "invalid webhook signature")
    try:
        body = json.loads(raw)
        scan_id = body.get('scan_id')
        if not isinstance(scan_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', scan_id):
            raise ValueError('invalid scan ID')
        rows = body.get('result_rows')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 100 or not all(isinstance(row, dict) for row in rows):
            raise ValueError('invalid result rows')
        queue.callback(scan_id, translate_callback(body))
    except (ValueError, IntakeError):
        raise HTTPException(400, 'invalid scanner callback')


class ReportCode(BaseModel):
    code: str = Field(pattern=r"^[0-9]{6}$")


def _report_session(request: Request) -> str | None:
    return request.cookies.get('bb_report_session')


def _report_csrf_ok(request: Request) -> bool:
    """Report verification changes must originate from the first-party viewer."""
    origin = request.headers.get('origin')
    referer = request.headers.get('referer')
    allowed = {'https://backbond.ai', 'https://www.backbond.ai'}
    if origin:
        return origin.rstrip('/') in allowed
    if referer:
        return any(referer.startswith(value + '/') for value in allowed)
    return False


@app.post("/scan/v1/reports/{token}/verification")
def request_report_verification(token: str, request: Request):
    if not _report_csrf_ok(request):
        raise HTTPException(403, "verification failed")
    issued = store.issue_report_code(token)
    if issued:
        recipient, value = issued
        nonce, code = value.split('.', 1)
        _send_report_code(recipient, code)
        response = JSONResponse({"status": "If this report is available, a code was sent.",
                                 "recipient_hint": store.report_recipient_hint(token)})
        response.set_cookie('bb_report_challenge', nonce, secure=True, httponly=True,
                            samesite='strict', max_age=600, path=f'/scan/v1/reports/{token}')
        return response
    return {"status": "If this report is available, a code was sent."}


@app.post("/scan/v1/reports/{token}/session")
def create_report_session(token: str, body: ReportCode, request: Request):
    if not _report_csrf_ok(request):
        raise HTTPException(403, "verification failed")
    nonce = request.cookies.get('bb_report_challenge') or ''
    session = store.verify_report_code(token, nonce + '.' + body.code)
    if not session:
        raise HTTPException(403, "verification failed")
    response = JSONResponse({"status": "verified"})
    response.set_cookie('bb_report_session', session, secure=True, httponly=True,
                        samesite='strict', max_age=1800, path=f'/scan/v1/reports/{token}')
    response.delete_cookie('bb_report_challenge', path=f'/scan/v1/reports/{token}')
    return response


@app.delete("/scan/v1/reports/{token}/session", status_code=204)
def delete_report_session(token: str, request: Request):
    if not _report_csrf_ok(request):
        raise HTTPException(403, "verification failed")
    store.revoke_report_session(_report_session(request))
    response = Response(status_code=204)
    response.delete_cookie('bb_report_session', path=f'/scan/v1/reports/{token}')
    return response


@app.get("/scan/v1/reports/{token}")
def report(token: str, request: Request):
    if not store.authorize_report(token, _report_session(request)):
        raise HTTPException(404, "report not found")
    value = store.report(token)
    # Read sanitized existing reports through the public API without opening its database.
    if value is None and os.environ.get('QA_REVIEW_EXISTING_REPORTS') == '1' and re.fullmatch(r'[a-f0-9]{32}', token):
        with httpx.Client(timeout=15) as client:
            upstream = client.get('http://127.0.0.1:8790/qa/v1/reports/' + token)
        if upstream.status_code == 200:
            value = upstream.json()
    if not value:
        raise HTTPException(404, "report not found")
    return value


@app.post("/scan/v1/delete")
def delete_scan(body: StatusRequest):
    if not store.delete_by_status_token(body.status_token):
        raise HTTPException(404, "scan not found")
    return {"status": "deleted"}


def seed_codes() -> None:
    store.put_code(Code("HACKERU-QA-1", "hackeru", "HackerU", "standard", 300, 5, "2027-01-07"))
    store.put_code(Code("HACKERU-GUY", "hackeru", "HackerU", "instructor", 1, 25, "2026-12-31"))


if os.environ.get("QA_SEED_CODES") == "1":
    seed_codes()
