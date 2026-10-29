"""FastAPI surface for the private partner QA intake service."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, model_validator
from bac_intake import BACIntake, BACUnconfirmed
from findings_intake import FindingsIntake
from findings_client import FindingsReports
from operations_client import OperationsClient, OperationsOutbox
from controlled_admission import AdmissionClosed
from qa_runtime import DispatchNotSent, DispatchUnknown, QueueFull, ScanQueue
from report_release import ReportRelease, RevisionTampered
from report_mail_access import ReportMailAccess
from report_mail_routes import router as report_mail_router

from qa_service import (Code, ConnectionProofs, IntakeError, IntakeStore, RateGate,
                        report_failure, safe_provenance, scanner_payload, translate_callback, translate_tests, verify_webhook)

DB_PATH = os.environ.get("QA_DB_PATH", "/var/lib/backbond-qa/qa.sqlite")
SCANNER_URL = os.environ.get("QA_SCANNER_URL", "https://scanner.backbond.ai/scanner/v1")
PUBLIC_API = os.environ.get("QA_PUBLIC_API", "https://api.backbond.ai")
WEBHOOK_SECRET = os.environ.get("QA_WEBHOOK_SECRET", "")
PUBLIC_CODE = "PUBLIC"
PUBLIC_PARTNER = "public"
DEFAULT_STARTER_PACK_ID = "starter-100@4"
STARTER_PACK_IDS = frozenset({
    "starter-100@1", "starter-100@2", "starter-100@3", "starter-100@4",
    "starter-100@5",
    "starter-voice-50@1",
})
# are-scanner api/scan_handlers.py: pack starter-100@1 (100 messages) and one
# CONNECTIVITY_PROMPT ("Reply with exactly OK.") per connection check.
SCAN_MESSAGES = 100
CONNECTION_CHECK_MESSAGES = 1
PROOF_MESSAGE = "Your connection details changed or the connection test expired. Test the connection again."

def _operations_client():
    """Operations Starter intake (QA_OPERATIONS_BASE_URL https + QA_OPERATIONS_TOKEN); unset = no-op."""
    return OperationsClient.from_env(post=httpx.post, get=httpx.get)


def _operations_enqueue(request_id, scan_id):
    operations.enqueue(bac, store, request_id=request_id, scan_id=scan_id, client=_operations_client())


def _bac_mode():
    if os.environ.get('QA_FINDINGS_REPORTS') == '1':
        return 'absent'
    keys = ('QA_BAC_URL', 'QA_BAC_COMPANY_ID', 'QA_BAC_LEAD_ID', 'QA_BAC_TOKEN')
    values = [os.environ.get(key, '').strip() for key in keys]
    if not any(values):
        return 'absent'
    if not all(values):
        return 'invalid'
    from urllib.parse import urlsplit
    try:
        url = urlsplit(values[0])
        valid = url.scheme in {'http', 'https'} and bool(url.hostname) and not url.username and not url.password
    except ValueError:
        valid = False
    return 'configured' if valid else 'invalid'


def worker_tick():
    """Advance native Findings and exact reviewed delivery, or legacy compatibility.

    Findings mode never invokes the retired Operations reporting chain.
    """
    if os.environ.get('QA_FINDINGS_REPORTS') == '1':
        with store._lock:
            active = list(store.db.execute("SELECT id,pack_version FROM scans WHERE status IN ('running','dispatch_unknown') AND scanner_id IS NOT NULL"))
        for row in active:
            findings.accept(row['id'], row['pack_version'])
        findings.capture(store, queue, _terminal_scan, _engine_progress)
        findings_reports.capture(store, _findings_accepted_total,
            selection=_findings_selection_row,
            since=os.environ.get('QA_FINDINGS_REPORTS_SINCE', ''),
            replay=tuple(filter(None, os.environ.get('QA_FINDINGS_REPLAY_SCAN_IDS', '').split(','))))
        findings_reports.tick(base_url=os.environ.get('QA_BAC_URL', ''), token=os.environ.get('QA_BAC_TOKEN', ''),
            http=httpx, mail_credentials=_findings_mail_credentials, recover_selection=_findings_selection)
        queue.expire_pending()
        return
    if _bac_mode() == "invalid":
        return
    if _bac_mode() == "configured" or (store is not None and bac.db.execute(
            "SELECT 1 FROM bac_scan_bindings LIMIT 1").fetchone()):
        bac.reconcile_pending(limit=1)
        queue.recover_bac(bac)
        bac.capture_scans(store, queue, _terminal_scan, fetch_progress=_engine_progress,
                          on_finished=_operations_enqueue)
        bac.flush_events(limit=1)
        operations.flush(_operations_client(), limit=1)
        operations.poll_status(_operations_client())
    if _bac_mode() != "configured":
        findings.capture(store, queue, _terminal_scan, _engine_progress)
    queue.expire_pending()


_worker_wakeup = threading.Event()


@asynccontextmanager
async def lifespan(app):
    stopped = threading.Event()
    def worker():
        while not stopped.is_set():
            _worker_wakeup.clear()
            try:
                worker_tick()
            except Exception:
                pass  # Durable work retries; private evidence never enters logs.
            _worker_wakeup.wait(10)
    def changed():
        findings_reports.expedite()
        _worker_wakeup.set()
    thread = threading.Thread(target=worker, daemon=True)
    events_thread = None
    if os.environ.get('QA_FINDINGS_REPORTS') == '1' and _findings_configured():
        from findings_events import IntakeEvents
        events = IntakeEvents(os.environ.get('QA_BAC_URL', ''), os.environ.get('QA_BAC_TOKEN', ''), httpx, changed)
        events_thread = threading.Thread(target=events.run, args=(stopped,), daemon=True)
        events_thread.start()
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        _worker_wakeup.set()
        queue.close()
        thread.join(timeout=1)
        if events_thread:
            events_thread.join(timeout=1)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(CORSMiddleware,
                   allow_origins=["https://backbond.ai", "https://www.backbond.ai"],
                   allow_credentials=True,
                   allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                   allow_headers=["Content-Type"])
store = IntakeStore(DB_PATH)
findings = FindingsIntake(DB_PATH)
findings_reports = FindingsReports(DB_PATH)
bac = BACIntake(DB_PATH, base_url=os.environ.get('QA_BAC_URL', ''),
                company_id=os.environ.get('QA_BAC_COMPANY_ID', ''),
                owner_id=os.environ.get('QA_BAC_LEAD_ID', ''),
                token=os.environ.get('QA_BAC_TOKEN', ''), post=httpx.post, get=httpx.get)
operations = OperationsOutbox(DB_PATH)
release = ReportRelease(os.environ.get('QA_RELEASE_DB_PATH', DB_PATH),
                        lead_actor_id=lambda: os.environ.get('QA_RELEASE_LEAD_ACTOR_ID', ''))
# BEAAA-43820: disabled until the retained release and mail grant holds are lifted.
def _report_mail_gateway():
    # Packaged from the maintained Scanner resolver, never a second credential store.
    from edge_gateway import EdgeGateway
    if not os.environ.get('ARE_KEY_STORE_PATH'):
        return None
    return EdgeGateway.from_env()


def _report_mail_settings():
    return dict(enabled=os.environ.get('QA_REPORT_MAIL_ENABLED') == '1',
        tenant_id=os.environ.get('QA_REPORT_MAIL_TENANT_ID', ''),
        key_id=os.environ.get('QA_REPORT_MAIL_KEY_ID', ''),
        founders=list(filter(None, os.environ.get('QA_REPORT_MAIL_FOUNDERS', '').split(','))),
        public_origin=PUBLIC_API)


report_mail = ReportMailAccess(DB_PATH)
app.include_router(report_mail_router(outbox=report_mail, gateway=_report_mail_gateway,
    settings=_report_mail_settings, credentials=lambda: _findings_mail_credentials()))
proofs = ConnectionProofs(ttl=int(os.environ.get('QA_CONNECTION_PROOF_TTL', '900')))
check_gate = RateGate(email=(int(os.environ.get('QA_CHECKS_PER_EMAIL_HOUR', '10')), 3600),
                      ip=(int(os.environ.get('QA_CHECKS_PER_IP_HOUR', '30')), 3600),
                      all=(int(os.environ.get('QA_CHECKS_PER_HOUR', '300')), 3600))
public_gate = RateGate(ip=(int(os.environ.get('QA_PUBLIC_SCANS_PER_IP_DAY', '3')), 86400))
report_issue_gate = RateGate(ip=(int(os.environ.get('QA_REPORT_CODES_PER_IP_HOUR', '5')), 3600),
                             all=(int(os.environ.get('QA_REPORT_CODES_PER_HOUR', '300')), 3600))


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
    transport: str = Field(pattern="^(rest|openai_chat|json_map|ag_ui|vapi|kaltura_genie|kaltura_genie_browser|amazon_quick)$")
    auth: Auth
    model: str | None = None
    system_prompt: str | None = Field(default=None, max_length=32000)
    request_template: str | None = Field(default=None, max_length=32000)
    response_path: str | None = None
    vapi_assistant_id: str | None = None
    # An omitted value deliberately remains absent.  This lets surgical live
    # upgrades preserve the installed service's existing default pack.
    pack_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def consent_and_transport(self, info: ValidationInfo):
        if self.pack_id is not None and self.pack_id not in STARTER_PACK_IDS:
            raise ValueError("pack_id must be a registered Starter pack")
        endpoint_host = (urlsplit(self.endpoint_url).hostname or "").lower().rstrip(".")
        if endpoint_host in {
            "backbond.ai", "www.backbond.ai", "scanner.backbond.ai", "api.backbond.ai",
        }:
            verify = (info.context or {}).get('controlled_target_verifier')
            if verify is None:
                raise ValueError("endpoint_url must not target a BackBond scanner service")
            verify(self.model_dump())
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
        if self.transport == "kaltura_genie":
            if not re.fullmatch(r"genie\.[a-z0-9-]+\.ovp\.kaltura\.com", endpoint_host):
                raise ValueError("kaltura_genie requires a regional Kaltura Genie host")
            if urlsplit(self.endpoint_url).path.rstrip('/') != '/assistant/converse':
                raise ValueError("kaltura_genie requires /assistant/converse")
            if self.auth.type != "bearer":
                raise ValueError("kaltura_genie requires a scoped KS in the secure auth field")
        if self.transport == "kaltura_genie_browser":
            if self.endpoint_url != "https://learning.kaltura.com/":
                raise ValueError("kaltura_genie_browser requires the approved public URL")
            if self.auth.type != "none":
                raise ValueError("kaltura_genie_browser requires auth type none")
        if self.transport == "amazon_quick":
            from harness.amazon_quick import validate_endpoint
            from harness.runner import RunnerError
            try:
                validate_endpoint(self.endpoint_url, self.auth.type, self.model_dump())
            except RunnerError:
                raise ValueError("amazon_quick requires a registered pilot URL and bearer token") from None
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
    dynamic = Path('/var/lib/backbond-qa/partner-code-tenants.json')
    if dynamic.is_file():
        for tenant, token in json.loads(dynamic.read_text()).items():
            if tenant not in mapping:
                mapping[tenant] = token
    return mapping


def _headers(data: dict | None = None) -> dict[str, str]:
    token = os.environ.get("QA_SCANNER_TOKEN", "")
    mapping = _tenant_tokens()
    if data and data.get('tenant_id') in mapping:
        token = mapping[data['tenant_id']]
    return {"Authorization": f"Bearer {token}"} if token else {}


def _link_error(exc: Exception) -> str:
    # Never include exception text: HTTP errors can contain secrets and targets.
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return ('authentication_error' if status in (401, 403) else
                'upstream_client_error' if 400 <= status < 500 else 'upstream_server_error')
    if isinstance(exc, httpx.TimeoutException):
        return 'timeout'
    if isinstance(exc, httpx.ConnectError):
        return 'connect_error'
    if isinstance(exc, httpx.HTTPError):
        return 'transport_error'
    if isinstance(exc, (ValueError, TypeError, AttributeError)):
        return 'invalid_response_or_config'
    if isinstance(exc, OSError):
        return 'local_config_error'
    return 'internal_error'


@app.get('/scan/v1/scanner-link-health')
def scanner_link_health():
    # An empty payload cannot run a target call; 422 proves authenticated routing
    # and request validation, unlike the public release-identity endpoint.
    try:
        with httpx.Client(timeout=5) as client:
            response = client.post(f'{SCANNER_URL}/connectivity-check', json={}, headers=_headers())
        status = response.status_code
        ok = status == 422
        return JSONResponse(status_code=200 if ok else 503, content={
            'ok': ok, 'stage': 'intake_to_scanner', 'upstream_status': status})
    except Exception as exc:
        return JSONResponse(status_code=503, content={
            'ok': False, 'stage': 'intake_to_scanner', 'error_class': _link_error(exc)})


def _scanner_check(data: dict) -> tuple[bool, str | None]:
    timeout = 320 if data.get('agent_endpoint', {}).get('transport') == 'amazon_quick' else 35
    with httpx.Client(timeout=timeout) as client:
        response = client.post(f"{SCANNER_URL}/connectivity-check", json=data, headers=_headers(data))
    if response.status_code >= 400:
        response.raise_for_status()
    body = response.json()
    error = body.get("error")
    if not isinstance(error, str) or not (error in {'timeout', 'redirect refused', 'connection failed'} or re.fullmatch(r'HTTP [1-5][0-9]{2}', error)):
        error = 'Connection check failed. Check the endpoint, authentication and response settings.'
    from harness.connection_diagnostics import safe_excerpt
    detail = {}
    if body.get('failed_stage') in {'reachability', 'transport', 'response_parse', 'auth'}:
        detail['failed_stage'] = body['failed_stage']
    if body.get('error_class') in {'dns', 'tls', 'timeout', 'connection_refused', 'connection_reset'}:
        detail['error_class'] = body['error_class']
    for key, low, high in [('target_status', 100, 599), ('elapsed_ms', 0, 3600000)]:
        if type(body.get(key)) is int and low <= body[key] <= high:
            detail[key] = body[key]
    if isinstance(body.get('response_excerpt'), str):
        detail['response_excerpt'] = safe_excerpt(body['response_excerpt'], [data.get('credential_secret', '')])
    return bool(body.get("success")), ({'error': error, **detail} if detail else error)


def _scanner_start(data: dict) -> dict:
    """Raise DispatchNotSent only when the scanner provably did not start the scan.
    Read/write timeouts, 5xx and unreadable 2xx bodies are ambiguous and propagate."""
    try:
        with httpx.Client(timeout=20) as client:
            identity = {}
            try:
                observed = client.get(f"{SCANNER_URL}/release-identity", timeout=5)
                if observed.status_code == 200:
                    raw_identity = observed.json()
                    identity = safe_provenance({
                        'scanner_version': raw_identity.get('commit_sha'),
                        'scanner_release_commit': raw_identity.get('commit_sha'),
                        'scanner_image_digest': raw_identity.get('image_digest'),
                        'scanner_identity_observed_at': datetime.now(timezone.utc).isoformat(),
                    })
            except (httpx.HTTPError, ValueError):
                pass
            response = client.post(f"{SCANNER_URL}/scans", json=data, headers=_headers(data))
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise DispatchNotSent() from None
    if 400 <= response.status_code < 500:
        raise DispatchNotSent()
    response.raise_for_status()
    started = response.json()
    if isinstance(started, dict):
        identity.update(safe_provenance({'seed': started.get('seed'),
            'corpus_snapshot_hash': started.get('corpus_snapshot_hash')}))
        started['provenance'] = identity
    return started


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


def _findings_mail_credentials():
    path = _mail_credentials_path()
    return json.loads(path.read_text()) if path else {}


def _findings_selection(ident):
    with store._lock:
        row = store.db.execute('SELECT scanner_id,partner FROM scans WHERE id=?', (ident,)).fetchone()
    if not row:
        raise ValueError('unknown_selection_owner')
    return _findings_selection_row(dict(row))


def _findings_selection_row(row):
    tenant = 'partner-' + row['partner']
    response = httpx.get(SCANNER_URL.removesuffix('/scanner/v1') + '/scan/' + row['scanner_id'],
        headers=_headers({'tenant_id': tenant}), timeout=15, follow_redirects=False)
    if response.status_code != 200:
        raise ValueError('selection_transport_unavailable')
    value = response.json().get('selection')
    if not isinstance(value, dict) or value.get('tenant_id') != tenant or value.get('scanner_id') != row['scanner_id']:
        raise ValueError('selection_owner_unconfirmed')
    return value


def _findings_configured():
    try:
        url = urlsplit(os.environ.get('QA_BAC_URL', ''))
        since = datetime.fromisoformat(os.environ.get('QA_FINDINGS_REPORTS_SINCE', ''))
        return bool(url.scheme == 'https' and url.hostname and not url.username and not url.password
                    and not url.query and not url.fragment and since.tzinfo
                    and os.environ.get('QA_BAC_TOKEN') and _mail_configured())
    except ValueError:
        return False


def _findings_accepted_total(row):
    for source in (findings, bac):
        total = source.latest_progress(row['id']).get('accepted_pack_total')
        if type(total) is int and total > 0:
            return total
    # Terminal webhooks can precede our first progress poll. Read the accepted
    # denominator from Scanner; never infer it from the pack name.
    response = httpx.get(SCANNER_URL + '/scans/' + row['scanner_id'] + '/progress',
        headers=_headers({'tenant_id': 'partner-' + row['partner']}), timeout=10, follow_redirects=False)
    if response.status_code != 200:
        return None
    total = response.json().get('accepted_pack_total')
    return total if type(total) is int and total > 0 else None


def _send_report_code(to: str, code: str) -> None:
    path = _mail_credentials_path()
    if not path or not path.is_file():
        raise RuntimeError("Report mail is not configured")
    value = json.loads(path.read_text())
    from report_mail_transport import send_mail
    send_mail(value, recipients=[to], subject='Your BackBond report verification code',
              text=f'Your verification code is {code}. It expires in 10 minutes.')


# Optional protected startup configuration; loading never arms a reservation.
from controlled_startup import load_operator
controlled_operator = load_operator()


def _controlled_claim(request, payload):
    if controlled_operator is None:
        return None
    # Both general admission holds must remain closed, including for the operator.
    if _flag('QA_TERMS_APPROVED', '0') or os.environ.get('QA_READY') == '1':
        raise HTTPException(503, 'Controlled admission requires general holds')
    try:
        controlled_operator.claim_http(request.headers, payload)
    except AdmissionClosed:
        raise HTTPException(403, 'Controlled admission unavailable') from None
    return controlled_operator


def _controlled_dispatch(scan_id, payload):
    if controlled_operator is not None:
        execution = dict(payload)
        opaque_id = execution.pop('controlled_permit_id', None)
        if opaque_id != getattr(controlled_operator, 'scanner_permit_id', None):
            raise AdmissionClosed('Scanner permit binding unavailable')
        controlled_operator.before_dispatch(scan_id, execution)


queue = ScanQueue(store, lambda payload: _scanner_start(payload),
                   pending_ttl=int(os.environ.get('QA_QUEUE_TTL_SECONDS', '900')),
                   before_dispatch=_controlled_dispatch)


class CodeRequest(BaseModel):
    code: str = Field(max_length=128)


@app.post("/scan/v1/check-code")
async def check_code(body: CodeRequest):
    return store.check_code(body.code)


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default) == '1'


def _terms_version() -> str:
    return os.environ.get('QA_TERMS_VERSION', 'v0.2.2')


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


async def _controlled_request(request: Request, model):
    # No caller-provided flag can enable contextual validation. The adapter is
    # service-owned and remains absent in production until integrated activation.
    context = None
    if controlled_operator is not None:
        if _flag('QA_TERMS_APPROVED', '0') or os.environ.get('QA_READY') == '1':
            raise HTTPException(503, 'Controlled admission requires general holds')
        try:
            controlled_operator.authenticate_http(request.headers)
        except AdmissionClosed:
            raise HTTPException(403, 'Controlled admission unavailable') from None

        def verify(fields):
            resolved = _resolve_code(fields['code'])
            fields.pop('connection_proof', None)
            fields['code'], fields['partner'] = resolved['code'], resolved['partner']
            try:
                controlled_operator.verify_http(request.headers, _payload(fields))
            except AdmissionClosed:
                raise HTTPException(403, 'Controlled admission unavailable') from None
        context = {'controlled_target_verifier': verify}
    try:
        return model.model_validate(await request.json(), context=context)
    except (ValidationError, ValueError):
        raise HTTPException(422, 'Check the submitted fields and required consent.') from None


async def _scan_request(request: Request):
    return await _controlled_request(request, ScanRequest)


async def _connection_request(request: Request):
    return await _controlled_request(request, ConnectionFields)


@app.post("/scan/v1/connection-checks")
def connection_check(body: Annotated[ConnectionFields, Depends(_connection_request)], request: Request):
    """Connectivity only: one message, no reservation, no quota, cannot start a scan."""
    if controlled_operator is None and os.environ.get('QA_READY') != '1':
        raise HTTPException(503, "QA scans are temporarily unavailable")
    if controlled_operator is not None:
        if _flag('QA_TERMS_APPROVED', '0') or os.environ.get('QA_READY') == '1':
            raise HTTPException(503, 'Controlled admission requires general holds')
        try:
            controlled_operator.authenticate_http(request.headers)
        except (AdmissionClosed, AttributeError):
            raise HTTPException(403, 'Controlled admission unavailable') from None
    resolved = _resolve_code(body.code)
    if not check_gate.allow(email=body.email.strip().lower(), ip=_client(request), all='all'):
        raise HTTPException(429, "Too many connection tests. Wait a few minutes and try again.")
    fields = body.model_dump()
    clean = dict(fields, code=resolved["code"], partner=resolved["partner"])
    payload = _payload(clean)
    if controlled_operator is not None:
        try:
            controlled_operator.verify_http(request.headers, payload)
            payload['controlled_permit_id'] = controlled_operator.scanner_permit_id
        except (AdmissionClosed, AttributeError):
            raise HTTPException(403, 'Controlled admission unavailable') from None
    try:
        ok, error = _scanner_check(payload)
    except Exception as exc:
        category = _link_error(exc)
        logging.getLogger(__name__).error('scanner_link_failure category=%s', category)
        fields.clear()
        return JSONResponse(status_code=502, content={
            'error': 'scanner unavailable', 'failed_stage': 'intake_to_scanner',
            'error_class': category})
    finally:
        payload.clear()
        clean.clear()
    try:
        if not ok:
            return {"ok": False, **(error if isinstance(error, dict) else {"error": error or "connection failed"})}
        return {"ok": True, "connection_proof": proofs.issue(fields), "expires_in": proofs.ttl}
    finally:
        fields.clear()


@app.post("/scan/v1/scans")
def start_scan(body: Annotated[ScanRequest, Depends(_scan_request)], request: Request):
    controlled = controlled_operator is not None
    if os.environ.get('QA_FINDINGS_REPORTS') == '1' and not _findings_configured():
        raise HTTPException(503, 'Scan reporting is temporarily unavailable')
    if not controlled and not _flag("QA_TERMS_APPROVED", "0"):
        raise HTTPException(503, "This review preview cannot start scans. Terms and launch approval are pending.")
    if _bac_mode() == "invalid":
        raise HTTPException(503, "BAC configuration is incomplete or invalid")
    if (_bac_mode() == "configured" and not _mail_configured()) or not WEBHOOK_SECRET or (not controlled and os.environ.get('QA_READY') != '1'):
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
    payload = _payload(clean)
    if controlled:
        if _flag('QA_TERMS_APPROVED', '0') or os.environ.get('QA_READY') == '1':
            raise HTTPException(503, 'Controlled admission requires general holds')
        try:
            controlled_operator.verify_http(request.headers, payload)
        except AdmissionClosed:
            raise HTTPException(403, 'Controlled admission unavailable') from None
    # Validate all non-consuming prerequisites before reserving the sole use.
    operator = controlled_operator
    if operator and legacy:
        raise HTTPException(503, "Controlled admission requires a connection proof")
    if operator and not getattr(operator, 'scanner_permit_id', None):
        raise HTTPException(503, 'Controlled Scanner binding unavailable')
    try:
        reservation = store.reserve(code=clean["code"], email=str(clean["email"]),
            agent_label=clean["agent_label"], endpoint_url=clean["endpoint_url"],
            transport=clean["transport"], terms_version=body.terms_version or "unversioned-legacy",
            improvement_consent=body.consent_improvement, pack_version=body.pack_id,
            provenance={'seed': int(os.environ.get('QA_SCANNER_CORPUS_SEED', '42')),
                        'model_versions': {'target_requested': clean['model']} if clean.get('model') else {}})
    except IntakeError as exc:
        raise HTTPException(429 if "limit" in str(exc) or "used" in str(exc) else 400, str(exc))
    try:
        operator = _controlled_claim(request, payload)
    except HTTPException:
        store.cancel_unsent(reservation["scan_id"])
        payload.clear()
        clean.clear()
        raise
    if operator:
        payload['controlled_permit_id'] = operator.scanner_permit_id
    connection_passed = not legacy
    try:
        if operator:
            try:
                operator.bind_scan(reservation["scan_id"])
            except Exception:
                # Provably before queue/Scanner I/O. Retain the consumed permit
                # and scan record; release the unsent slot via terminal status.
                store.launch_failed(reservation["scan_id"])
                return {"status": "launch_failed", "status_token": reservation["status_token"]}
        if legacy:
            ok, error = _scanner_check(payload)
            if not ok:
                store.connection_failed(reservation["scan_id"])
                raise HTTPException(400, error or "connection failed")
            connection_passed = True
        scan_ref = reservation['scan_id']
        intake_ref = scan_ref
        try:
            if _bac_mode() == "configured":
                intake_ref = bac.retry_identity(scan_ref, clean)
                bac.acknowledge(intake_ref, customer_ref='intake:' + intake_ref,
                                agent_ref='intake:' + intake_ref,
                                scope_ref=body.pack_id,
                                code_ref='intake:' + intake_ref)
                status = queue.submit(scan_ref, payload)
            else:
                findings.accept(scan_ref, body.pack_id)
                status = queue.submit(scan_ref, payload)
        except BACUnconfirmed:
            try:
                queue.defer_bac(scan_ref, intake_ref, payload)
            except QueueFull:
                store.cancel_unsent(scan_ref)
                raise HTTPException(503, "Intake acknowledgment pending. Submit again later.")
            payload = {}  # Recovery owns the original credential-bearing payload.
            return {"status": "queued", "status_token": reservation["status_token"]}
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


@app.post("/scan/v1/retry-report")
def retry_report(body: StatusRequest):
    # Resolve both identity and tenant from the reservation, never from request fields.
    scan_id = _status(body.status_token)['scan_id']
    with store._lock:
        row = store.scan(scan_id)
        if not report_failure(row) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', row['scanner_id'] or ''):
            raise HTTPException(409, "report retry unavailable")
        headers = _headers({'tenant_id': 'partner-' + row['partner']})
        if not headers:
            raise HTTPException(503, "report retry unavailable")
        try:
            response = httpx.post(f"{SCANNER_URL}/scans/{row['scanner_id']}/retry-report",
                                  headers=headers, timeout=60, follow_redirects=False)
            if response.status_code != 200:
                raise HTTPException(503, "report retry unavailable")
            # Retry response is a receipt; read the existing customer result contract.
            value = _terminal_scan(row)
            if isinstance(value, dict):
                store.refresh_report_failure(scan_id, row['scanner_id'], value)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "report retry unavailable") from None
    _worker_wakeup.set()
    return {**_status(body.status_token), 'progress': _engine_progress(scan_id)}


_PROGRESS_FIELDS = {
    "state", "phase", "stage_label", "processed_count", "accepted_pack_total",
    "usable_count", "unavailable_count", "timing", "heartbeat_at", "updated_at",
    "last_activity_at", "current_test_id", "current_family",
    "failed_stage", "execution_completed", "report_status", "report_retry_allowed", "retry_allowed",
}


def _terminal_scan(row):
    # Existing authenticated customer result contract, never a probe/dispatch.
    headers = _headers({'tenant_id': f"partner-{row['partner']}"})
    if not headers:
        return None
    remote_id = row['scanner_id']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', remote_id):
        return None
    response = httpx.get(SCANNER_URL.removesuffix('/scanner/v1') + '/scan/' + remote_id,
                         headers=headers, timeout=5, follow_redirects=False)
    return response.json() if response.status_code == 200 else None


def _pack_total(pack_version) -> int | None:
    """Accepted size of a known starter pack id (``starter-100@4`` -> 100); unknown -> None."""
    match = re.fullmatch(r"starter-(?:voice-)?(\d+)@\d+", pack_version or "")
    return int(match.group(1)) if match and pack_version in STARTER_PACK_IDS else None


def _engine_progress(scan_id: str) -> dict:
    """Fetch the bounded owner-scoped engine projection, never its evidence rows."""
    row = store.scan(scan_id)
    if row and row['status'] == 'finished':
        tests = json.loads(row['tests_json'] or '[]')
        usable = sum(t.get('verdict') in {'pass', 'fail'} for t in tests)
        state = 'error' if row['outcome'] == 'failed' else 'incomplete' if row['outcome'] == 'incomplete' else 'finished'
        label = {'error': 'Scan failed', 'incomplete': 'Scan stopped before completion',
                 'finished': 'Results ready'}[state]
        value = {'state': state, 'phase': 'finished', 'stage_label': label,
                'processed_count': len(tests),
                'usable_count': usable, 'unavailable_count': len(tests) - usable,
                'updated_at': row['finished_at'], 'last_activity_at': row['finished_at']}
        # A report failure occurs after execution. Keep its evidence counters and
        # distinguish report-only recovery from submitting another scan.
        if str(dict(row).get('incomplete_reason') or '').startswith('report_preparation_error:'):
            value.update(state='incomplete', phase='report_preparation',
                         stage_label='Scan completed; report preparation failed',
                         failed_stage='report_preparation', execution_completed=True, report_status='failed',
                         report_retry_allowed=True, retry_allowed=False)
        # Terminal totals come only from recorded Scanner progress, never the pack name (f7dfa14ba).
        observed = (findings if findings.accepted_scope(scan_id) else bac).latest_progress(scan_id)
        # Failed runs may carry no report rows. Retain the last confirmed counters
        # as one snapshot, so processed = usable + unavailable remains consistent.
        if state in {'error', 'incomplete'}:
            counters = {key: observed.get(key) for key in
                        ('processed_count', 'usable_count', 'unavailable_count')}
            if (all(type(n) is int and n >= 0 for n in counters.values())
                    and counters['processed_count'] == counters['usable_count'] + counters['unavailable_count']
                    and counters['processed_count'] >= value['processed_count']):
                value.update(counters)
        accepted_total = observed.get('accepted_pack_total')
        if accepted_total is not None:
            value['accepted_pack_total'] = accepted_total
        return value
    if not row or not row["scanner_id"] or row["status"] not in {"running", "dispatch_unknown"}:
        return {}
    tenant_id = f"partner-{row['partner']}"
    try:
        with httpx.Client(timeout=5) as client:
            response = client.get(
                f"{SCANNER_URL}/scans/{row['scanner_id']}/progress",
                headers=_headers({"tenant_id": tenant_id}),
            )
        if response.status_code != 200:
            return {}
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: payload[key] for key in _PROGRESS_FIELDS if key in payload}


def _iso_max(*values):
    stamps = []
    for value in values:
        try:
            stamps.append((datetime.fromisoformat(value), value))
        except (TypeError, ValueError):
            pass
    return max(stamps)[1] if stamps else None


def _merge_lifecycle(lifecycle: dict, scan_id: str, scan_status: str) -> dict:
    """starter_lifecycle@1: intake_status from the BAC intake owner (42595); report_status and
    delivery_status from the Operations workflow's last good status snapshot once Operations has
    acknowledged the BAC-bound result. Missing acknowledgment stays pending; local release
    records are never an authority for BAC-bound scans. Store errors read unavailable."""
    if os.environ.get('QA_FINDINGS_REPORTS') == '1':
        return findings_reports.lifecycle(scan_id)
    try:
        if bac.is_bound(scan_id):
            released = operations.public_lifecycle(scan_id)
            if released is None:
                released = {'report_status': 'awaiting_results', 'delivery_status': 'not_ready',
                            'updated_at': None}
        else:
            released = release.public_lifecycle(scan_id, scan_failed=scan_status in ('failed', 'incomplete'))
    except Exception:
        released = {'report_status': 'unavailable', 'delivery_status': 'unavailable', 'updated_at': None}
    return {**lifecycle, 'report_status': released['report_status'],
            'delivery_status': released['delivery_status'],
            'updated_at': _iso_max(lifecycle.get('updated_at'), released['updated_at'])}


def _acknowledged_intake(scan_id):
    with bac.lock:
        return bac.db.execute('SELECT i.payload FROM bac_scan_bindings b JOIN bac_intake i '
                                  'ON i.request_id=b.request_id WHERE b.scan_id=? AND i.state=\'acknowledged\'',
                                  (scan_id,)).fetchone()


@app.post("/scan/v1/scan-status")
def scan_status(body: StatusRequest):
    if _bac_mode() == "invalid":
        raise HTTPException(503, "BAC configuration is incomplete or invalid")
    if _bac_mode() == "configured" or not findings.accepted_scope(_status(body.status_token)["scan_id"]):
        # POST so the bearer-style token never lands in access-log URLs.
        value = _status(body.status_token)
        progress = _engine_progress(value["scan_id"])
        if progress:
            value.update(progress)
            value["status"] = progress.get("state", value["status"])
        value["lifecycle"] = _merge_lifecycle(bac.public_lifecycle(value["scan_id"]),
                                              value["scan_id"], value["status"])
        scan_id = value.pop('scan_id')
        # Public reference is a stable, non-authorizing digest of the persisted reservation.
        # Neither the internal case identity nor the bearer status token is exposed.
        value['request_reference'] = 'BB-' + hashlib.sha256(('starter-public:' + scan_id).encode()).hexdigest()[:20]
        accepted = _acknowledged_intake(scan_id)
        try:
            scope = json.loads(json.loads(accepted[0])['description']).get('scope_ref') if accepted else None
        except (KeyError, TypeError, ValueError):
            scope = None
        value['accepted_scope_version'] = scope if scope in STARTER_PACK_IDS else None
        value['last_confirmed_progress_at'] = bac.confirmed_progress_at(scan_id)
        with queue.lock:
            queued = scan_id in queue.pending
        value['queue_acknowledged'] = bool(queued and accepted and value['status'] == 'queued')
        value['cancel_available'] = value['queue_acknowledged']
        return value
    else:
        # POST so the bearer-style token never lands in access-log URLs.
        value = _status(body.status_token)
        progress = _engine_progress(value["scan_id"])
        if progress:
            value.update(progress)
            value["status"] = progress.get("state", value["status"])
        value["lifecycle"] = findings_reports.lifecycle(value['scan_id']) if os.environ.get('QA_FINDINGS_REPORTS') == '1' else {"intake_status": "accepted",
                              "report_status": "awaiting_findings_handoff",
                              "delivery_status": "not_ready", "updated_at": None, "version": "starter_lifecycle@1"}
        scan_id = value.pop('scan_id')
        # Public reference is a stable, non-authorizing digest of the persisted reservation.
        # Neither the internal case identity nor the bearer status token is exposed.
        value['request_reference'] = 'BB-' + hashlib.sha256(('starter-public:' + scan_id).encode()).hexdigest()[:20]
        scope = findings.accepted_scope(scan_id)
        value['accepted_scope_version'] = scope if scope in STARTER_PACK_IDS else None
        value['last_confirmed_progress_at'] = findings.confirmed_progress_at(scan_id)
        with queue.lock:
            queued = scan_id in queue.pending
        value['queue_acknowledged'] = bool(queued and scope and value['status'] == 'queued')
        value['cancel_available'] = value['queue_acknowledged']
        return value


@app.post("/scan/v1/scan-cancel")
def scan_cancel(body: StatusRequest):
    if _bac_mode() == "invalid":
        raise HTTPException(503, "BAC configuration is incomplete or invalid")
    if _bac_mode() == "configured" or not findings.accepted_scope(_status(body.status_token)["scan_id"]):
        value = _status(body.status_token)
        if value["status"] == "queued":
            if not _acknowledged_intake(value["scan_id"]):
                raise HTTPException(409, "Request confirmation is pending; cancellation is unavailable.")
            with queue.lock:
                if value["scan_id"] in queue.pending and queue.cancel(value["scan_id"]):
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
    else:
        value = _status(body.status_token)
        if value["status"] == "queued":
            with queue.lock:
                if value["scan_id"] in queue.pending and queue.cancel(value["scan_id"]):
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
        terminal_failure = (str(body.get('status') or '').lower() == 'failed'
                            and body.get('incomplete') is True
                            and bool(body.get('incomplete_reason')))
        if (not isinstance(rows, list) or len(rows) > 100
                or (not rows and not terminal_failure)
                or not all(isinstance(row, dict) for row in rows)):
            raise ValueError('invalid result rows')
        queue.callback(scan_id, translate_callback(body))
        _worker_wakeup.set()
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


def _report_access(token: str) -> str | None:
    """Only published native SHIP files or already-mailed legacy reports open.

    Native files retain the independently approved bytes and authenticate the
    info mailbox. BAC scan results alone never enable the legacy local renderer.
    """
    if findings_reports.native.publication(token):
        return 'native_findings'
    scan_id = store.scan_for_report(token)
    if not scan_id:
        return None
    if bac.is_bound(scan_id):
        # Operations owns the final artifact; a local approval cannot open a customer link.
        return None
    return 'legacy' if store.legacy_released(scan_id) else None


def _report_released(token: str) -> bool:
    return _report_access(token) is not None


@app.post("/scan/v1/reports/{token}/verification")
def request_report_verification(token: str, request: Request):
    # Native publications authenticate the info mailbox; historical legacy
    # reports retain their original recipient. Unapproved scans stay closed.
    access = _report_access(token)
    if access not in {'legacy', 'native_findings'}:
        raise HTTPException(404, "report_not_released")
    if not _report_csrf_ok(request):
        raise HTTPException(403, "verification failed")
    client_key = _client(request)
    issued = ((findings_reports.native.issue_code(token, client_key)
               if access == 'native_findings' else store.issue_report_code(token, request_key=client_key))
              if report_issue_gate.allow(ip=client_key, all='all') else None)
    nonce = secrets.token_hex(16)
    if issued:
        recipient, value = issued
        nonce, code = value.split('.', 1)
        try:
            _send_report_code(recipient, code)
        except Exception:
            # Delivery state must not become a report/recipient enumeration oracle.
            pass
    response = JSONResponse({"status": "If this report is available, a code was sent."})
    response.set_cookie('bb_report_challenge', nonce, secure=True, httponly=True,
                        samesite='strict', max_age=600, path=f'/scan/v1/reports/{token}')
    return response


@app.post("/scan/v1/reports/{token}/session")
def create_report_session(token: str, body: ReportCode, request: Request):
    if not _report_released(token):
        raise HTTPException(404, "report_not_released")
    if not _report_csrf_ok(request):
        raise HTTPException(403, "verification failed")
    nonce = request.cookies.get('bb_report_challenge') or ''
    session = (findings_reports.native.verify_code(token, nonce + '.' + body.code)
               if _report_access(token) == 'native_findings' else
               store.verify_report_code(token, nonce + '.' + body.code, request_key=_client(request)))
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
    findings_reports.native.revoke(_report_session(request))
    response = Response(status_code=204)
    response.delete_cookie('bb_report_session', path=f'/scan/v1/reports/{token}')
    return response


@app.get("/scan/v1/reports/{token}")
def report(token: str, request: Request):
    session = _report_session(request)
    # Reject requests without credentials before consulting either the session
    # store or the report store.  Besides being the correct HTTP contract, this
    # prevents an unauthenticated request from becoming a report-token lookup
    # oracle through timing or storage side effects.
    if not session:
        raise HTTPException(401, "authentication required")
    if not _report_released(token):
        raise HTTPException(404, "report_not_released")
    if _report_access(token) == 'native_findings':
        if not findings_reports.native.authorize(token, session):
            raise HTTPException(403, 'report access denied')
        return JSONResponse({'report_kind': 'native_findings',
                             'html_url': PUBLIC_API + '/scan/v1/reports/' + token + '/artifacts/html',
                             'pdf_url': PUBLIC_API + '/scan/v1/reports/' + token + '/artifacts/pdf'},
                            headers={'Cache-Control': 'no-store'})
    if not store.authorize_report(token, session):
        raise HTTPException(403, "report access denied")
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


@app.get('/scan/v1/reports/{token}/artifacts/{kind}')
def native_report_artifact(token: str, kind: str, request: Request):
    session = _report_session(request)
    if not session:
        raise HTTPException(401, 'authentication required')
    if not findings_reports.native.authorize(token, session):
        raise HTTPException(403, 'report access denied')
    value = findings_reports.native.artifact(token, kind)
    if value is None:
        raise HTTPException(404, 'report not found')
    headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
               'Referrer-Policy': 'no-referrer',
               'Content-Disposition': 'inline; filename="BackBond-Findings-Report.' + kind + '"',
               'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; font-src data:; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors https://backbond.ai https://www.backbond.ai; sandbox"}
    return Response(value, media_type='application/pdf' if kind == 'pdf' else 'text/html', headers=headers)


RELEASE_ROLES = {'lead': ('QA_RELEASE_LEAD_TOKEN_SHA256', 'QA_RELEASE_LEAD_ACTOR_ID'),
                 'analyst': ('QA_RELEASE_ANALYST_TOKEN_SHA256', 'QA_RELEASE_ANALYST_ACTOR_ID'),
                 'reviewer': ('QA_RELEASE_REVIEWER_TOKEN_SHA256', 'QA_RELEASE_REVIEWER_ACTOR_ID')}
UNAVAILABLE_ROLE = {'lead': "release approval unavailable", 'analyst': "revision authoring unavailable",
                    'reviewer': "revision review unavailable"}


def _configured_roles() -> dict:
    configured = {}
    for role, (hash_env, actor_env) in RELEASE_ROLES.items():
        digest = os.environ.get(hash_env, '').strip().lower()
        actor = os.environ.get(actor_env, '')
        if re.fullmatch(r'[0-9a-f]{64}', digest) and actor:
            configured[role] = (digest, actor)
    return configured


def _matched_roles(authorization: str | None, configured: dict) -> set:
    if not authorization or not authorization.startswith('Bearer ') or not authorization[7:]:
        raise HTTPException(401, "authentication required")
    presented = hashlib.sha256(authorization[7:].encode()).hexdigest()
    return {role for role, (digest, _) in configured.items() if hmac.compare_digest(presented, digest)}


def _role_actor(authorization: str | None, required: str) -> tuple[str, bool]:
    """Authenticate a release role by exact token-hash match; the actor id never comes from the
    request body or headers. Returns (actor, holds_other_role). A role whose hash/actor env is
    unset fails closed (503)."""
    configured = _configured_roles()
    if required not in configured:
        raise HTTPException(503, UNAVAILABLE_ROLE[required])
    matched = _matched_roles(authorization, configured)
    if required not in matched:
        raise HTTPException(403, "wrong_role" if matched else "forbidden")
    return configured[required][1], bool(matched - {required})


def _revision(revision_id: str) -> dict:
    try:
        value = release.read_revision(revision_id) if re.fullmatch(r'rev_[0-9a-f]{32}', revision_id) else None
    except RevisionTampered:
        raise HTTPException(409, "revision_tampered")
    if not value:
        raise HTTPException(404, "revision not found")
    return value


class ReleaseDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # covers content + frozen artifact bytes
    nonce: str = Field(min_length=16, max_length=128)
    decision: str = Field(pattern="^(approve|reject)$")


class ReleaseReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    nonce: str = Field(min_length=16, max_length=128)
    decision: str = Field(pattern="^(approve|request_changes)$")


@app.get("/scan/v1/bac/revisions/{revision_id}")
def read_release_revision(revision_id: str, authorization: str | None = Header(default=None)):
    """Any configured release role (Analyst, reviewer, Lead) may read; nobody else."""
    configured = _configured_roles()
    if not configured:
        raise HTTPException(503, "release roles unavailable")
    if not _matched_roles(authorization, configured):
        raise HTTPException(403, "forbidden")
    return _revision(revision_id)


@app.post("/scan/v1/bac/revisions/{revision_id}/decision")
def decide_release_revision(revision_id: str, body: ReleaseDecision,
                            authorization: str | None = Header(default=None)):
    """Final report mutations belong exclusively to Operations."""
    raise HTTPException(410, "report_authority_moved_to_operations")


@app.post("/scan/v1/bac/revisions/{revision_id}/review")
def review_release_revision(revision_id: str, body: ReleaseReview,
                            authorization: str | None = Header(default=None)):
    """Final report mutations belong exclusively to Operations."""
    raise HTTPException(410, "report_authority_moved_to_operations")


class RevisionAuthoring(BaseModel):
    """Analyst-authored Starter report revision; evidence is named by its bound BAC case."""
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=200)
    case_id: str = Field(min_length=1, max_length=200)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # sha256 of the scan's stored tests
    summary: str = Field(min_length=1, max_length=8000)  # Analyst-written narrative
    recommendations: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list, max_length=20)


class CaseEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=200)
    case_id: str = Field(min_length=1, max_length=200)
    scan_id: str = Field(pattern=r"^[0-9a-f]{32}$")


@app.post("/scan/v1/bac/cases/evidence")
def read_case_evidence(body: CaseEvidenceRequest, authorization: str | None = Header(default=None)):
    """Analyst report-tool read: the bound case's evidence digest and ordinal verdicts only.

    POST so case references stay out of access-log URLs; same resolver checks as authoring."""
    _role_actor(authorization, 'analyst')
    try:
        digest = bac.evidence_digest(store, request_id=body.request_id, case_id=body.case_id,
                                     scan_id=body.scan_id)
        evidence = bac.resolve_evidence(store, request_id=body.request_id, case_id=body.case_id,
                                        scan_id=body.scan_id, revision_sha256=digest)
    except BACUnconfirmed:
        raise HTTPException(409, "evidence_unconfirmed")
    return {'request_id': evidence['request_id'], 'case_id': evidence['case_id'],
            'scan_id': evidence['scan_id'], 'evidence_sha256': evidence['revision_sha256'],
            'outcome': evidence['outcome'], 'results': evidence['results']}


@app.post("/scan/v1/bac/scans/{scan_id}/revisions", status_code=201)
def create_release_revision(scan_id: str, body: RevisionAuthoring,
                            authorization: str | None = Header(default=None)):
    """Final report mutations belong exclusively to Operations."""
    raise HTTPException(410, "report_authority_moved_to_operations")


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
