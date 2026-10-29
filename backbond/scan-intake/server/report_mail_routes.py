"""Bounded report submission and recipient-only downloads over the intake mailer."""
import hashlib
from html import escape
import json
import re
import secrets
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from report_mail_authorization import authorize_report_mail
from report_mail_outbox import canonical, validate
from report_mail_transport import send_mail
from report_mail_style import INTAKE_STYLESHEET

MAX_BODY = 35 * 1024 * 1024


async def bounded_json(request, limit):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, 'report_size_limit')
        body.extend(chunk)
    try:
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, 'invalid_report_payload') from None


def router(*, outbox, gateway, settings, credentials, transport=send_mail):
    routes = APIRouter()

    def available():
        config = settings()
        if not config.get('enabled'):
            raise HTTPException(503, 'report_mail_held')
        return config

    def principal(request, config):
        try:
            return authorize_report_mail(gateway(), request.headers.get('authorization', ''),
                tenant_id=config.get('tenant_id', ''), key_id=config.get('key_id', ''))
        except (PermissionError, AttributeError):
            raise HTTPException(403, 'report_mail_unauthorized') from None

    @routes.post('/scan/v1/reports/mail')
    async def submit(request: Request):
        config = available()
        actor = principal(request, config).key_id
        payload = await bounded_json(request, MAX_BODY)
        try:
            to, cc, _ = validate(payload, config.get('founders'))
            origin = config.get('public_origin', '')
            parsed = urlsplit(origin)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                    or parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
                raise ValueError('invalid_report_origin')
            ident = hashlib.sha256(canonical(dict(payload=payload, actor=actor)).encode()).hexdigest()
            link = origin.rstrip('/') + '/scan/v1/mailed-reports/' + ident
            # Link is a locator only. Each recipient must verify their own inbox.
            ident = await run_in_threadpool(outbox.submit, payload, actor=actor,
                founders=config['founders'], links={address: link + '?' + urlencode({'email': address}) for address in to + cc})
        except (ValueError, TypeError, KeyError):
            raise HTTPException(422, 'invalid_report_payload') from None
        receipt = await run_in_threadpool(outbox.deliver, ident, credentials(), transport=transport)
        return JSONResponse(receipt, status_code=200 if receipt['state'] == 'accepted' else 202,
                            headers={'Cache-Control': 'no-store'})

    def path(ident):
        if not re.fullmatch('[a-f0-9]{64}', ident):
            raise HTTPException(404, 'report_unavailable')
        return '/scan/v1/mailed-reports/' + ident

    def csrf(request, config):
        if request.headers.get('origin') != config.get('public_origin', '').rstrip('/'):
            raise HTTPException(403, 'verification_failed')

    @routes.post('/scan/v1/mailed-reports/{ident}/verification')
    async def issue(ident: str, request: Request):
        config = available()
        route = path(ident)
        csrf(request, config)
        value = await bounded_json(request, 1024)
        issued = await run_in_threadpool(outbox.issue, ident, value.get('email'),
            request_key=hashlib.sha256((request.client.host if request.client else 'unknown').encode()).hexdigest())
        challenge = secrets.token_urlsafe(32)
        if issued:
            challenge, code = issued
            try:
                await run_in_threadpool(transport, credentials(), recipients=[value['email']],
                    subject='Your BackBond report verification code', text='Your code is ' + code)
            except Exception:
                pass
        response = JSONResponse({'status': 'If this report is available, a code was sent.'},
                                headers={'Cache-Control': 'no-store'})
        response.set_cookie('bb_mailed_challenge', challenge, secure=True, httponly=True,
                            samesite='strict', max_age=600, path=route)
        return response

    @routes.post('/scan/v1/mailed-reports/{ident}/session')
    async def verify(ident: str, request: Request):
        config = available()
        route = path(ident)
        csrf(request, config)
        value = await bounded_json(request, 1024)
        code = value.get('code', '')
        if not isinstance(code, str) or not re.fullmatch('[0-9]{6}', code):
            raise HTTPException(403, 'verification_failed')
        session = await run_in_threadpool(outbox.verify, ident,
            request.cookies.get('bb_mailed_challenge', ''), code)
        if not session:
            raise HTTPException(403, 'verification_failed')
        response = JSONResponse({'status': 'verified'}, headers={'Cache-Control': 'no-store'})
        response.set_cookie('bb_mailed_session', session, secure=True, httponly=True,
                            samesite='strict', max_age=1800, path=route)
        response.delete_cookie('bb_mailed_challenge', path=route)
        return response

    @routes.get('/scan/v1/mailed-reports/{ident}')
    def download(ident: str, request: Request, format: str = 'html', email: str = ''):
        available()
        route = path(ident)
        session = request.cookies.get('bb_mailed_session', '')
        raw = outbox.artifact(ident, session, format) if session else None
        if raw is None:
            nonce = secrets.token_urlsafe(24)
            page = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BackBond report access</title>
<style nonce="NONCE">INTAKE_CSS</style>
<body><header class="site-head"><a class="brand-mark" href="/" aria-label="BackBond home">BackBond</a></header>
<main><section class="hero"><p class="eyebrow">The 100 Scan</p><h1>Verify your report access</h1></section>
<p>A verification code goes to the inbox that received this report.</p>
<form id="issue"><div class="field"><label>Email <input name="email" type="email" value="RECIPIENT" required autocomplete="email"></label></div>
<button>Send code</button></form>
<form id="verify"><div class="field"><label>Code <input name="code" required pattern="[0-9]{6}" autocomplete="one-time-code"></label></div>
<button>Verify</button></form><p id="status" role="status"></p>
<p id="downloads" hidden><a href="?format=pdf">Download PDF</a> · <a href="?format=html">Download HTML</a></p></main>
<script nonce="NONCE">const status=document.getElementById('status');
const recipient=new URLSearchParams(location.search).get('email');
if(recipient)document.querySelector('#issue input[name=email]').value=recipient;
async function post(action,body){return fetch(location.pathname+action,{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
document.getElementById('issue').onsubmit=async e=>{e.preventDefault();try{
const r=await post('/verification',{email:e.target.elements.email.value});
status.textContent=r.ok?'If this report is available, a code was sent.':'Unable to request a code.';
}catch(e){status.textContent='Unable to request a code.';}};
document.getElementById('verify').onsubmit=async e=>{e.preventDefault();try{
const r=await post('/session',{code:e.target.elements.code.value});
if(r.ok){location.replace(location.pathname);return;}
status.textContent='Verification failed. Request a new code if needed.';
document.getElementById('downloads').hidden=!r.ok;
}catch(e){status.textContent='Unable to verify the code.';}};</script></body></html>"""
            response = HTMLResponse(page.replace('NONCE', nonce).replace('INTAKE_CSS', INTAKE_STYLESHEET).replace('RECIPIENT', escape(email[:254], quote=True)), headers={
                'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                'X-Content-Type-Options': 'nosniff',
                'Content-Security-Policy': "default-src 'none'; script-src 'nonce-" + nonce +
                    "'; style-src 'nonce-" + nonce + "'; font-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})
            # Recover generically without revealing report existence or extending authority.
            response.delete_cookie('bb_mailed_session', path=route, secure=True,
                                   httponly=True, samesite='strict')
            return response
        # Render the existing report HTML in an opaque sandbox. Scripts and network access stay disabled.
        return Response(raw, media_type='application/pdf' if format == 'pdf' else 'text/html',
            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                'Content-Security-Policy': "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'",
                'Content-Disposition': ('attachment' if format == 'pdf' else 'inline') + '; filename="report.' + format + '"'})

    return routes
