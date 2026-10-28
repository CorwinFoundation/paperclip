"""Shared intake Resend transport; credentials remain in the sealed mail config.

Callers own durable claims/retries and recipient authorization. A provider acceptance
is not evidence of inbox delivery. Never include provider response bodies in errors.
"""
from __future__ import annotations

import base64
import re

import httpx

SENDER = 'BackBond <info@backbond.ai>'


def send_mail(config: dict, *, recipients: list[str], subject: str, text: str,
              idempotency_key: str | None = None, cc: list[str] | None = None,
              pdf: bytes | None = None) -> str:
    if config.get('provider') != 'resend' or not config.get('api_key'):
        raise RuntimeError('Report mail is not configured')
    addresses = recipients + (cc or [])
    if (not recipients or len(addresses) > 50 or any(
            not isinstance(a, str) or len(a) > 254 or
            not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', a)
            for a in addresses)):
        raise ValueError('Invalid report recipients')
    if not subject or len(subject) > 250 or '\r' in subject or '\n' in subject:
        raise ValueError('Invalid report subject')
    if idempotency_key is not None and not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', idempotency_key):
        raise ValueError('Invalid mail idempotency key')
    payload = {'from': SENDER, 'to': recipients, 'subject': subject, 'text': text}
    if cc:
        payload['cc'] = cc
    if pdf is not None:
        if not pdf.startswith(b'%PDF-') or len(pdf) > 20 * 1024 * 1024:
            raise ValueError('Invalid report PDF')
        payload['attachments'] = [{'filename': 'findings-report.pdf',
                                   'content': base64.b64encode(pdf).decode('ascii')}]
    headers = {'Authorization': 'Bearer ' + config['api_key']}
    if idempotency_key:
        headers['Idempotency-Key'] = idempotency_key
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.post('https://api.resend.com/emails', headers=headers, json=payload)
    if response.status_code not in {200, 201}:
        raise RuntimeError('Report mail delivery unavailable')
    try:
        message_id = response.json()['id']
        if not isinstance(message_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', message_id):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise RuntimeError('Report mail acceptance unconfirmed') from None
    return message_id
