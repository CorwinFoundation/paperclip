#!/opt/backbond/scan-venv/bin/python
"""BAC report-mail client. The scanner service owns authorization and delivery."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import urlsplit

import httpx


def build_payload(args):
    if args.mode == 'customer' and (not args.recipient or not re.fullmatch(r'BAC-[1-9][0-9]*', args.approval_ref or '')):
        raise ValueError('Customer delivery requires recipients and a BAC approval reference')
    if args.mode == 'founders' and (args.recipient or args.approval_ref):
        raise ValueError('Founders recipients come from server configuration')
    if any(not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', r) for r in args.recipient):
        raise ValueError('Invalid recipient')
    if not args.target.strip() or len(args.target) > 200 or len(args.note) > 2000:
        raise ValueError('Invalid target or note')
    from datetime import date
    date.fromisoformat(args.run_date)
    pdf_path, html_path = Path(args.pdf), Path(args.html)
    if pdf_path.stat().st_size > 20 * 1024 * 1024 or html_path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError('Report exceeds size limit')
    pdf, html = pdf_path.read_bytes(), html_path.read_bytes()
    if not pdf.startswith(b'%PDF-') or not html:
        raise ValueError('Invalid report artifacts')
    html.decode('utf-8')
    value = {'schema_version': 'report_mail@1', 'mode': args.mode,
             'target_name': args.target, 'run_date': args.run_date, 'note': args.note,
             'recipients': sorted(set(args.recipient)), 'approval_ref': args.approval_ref,
             'pdf_sha256': hashlib.sha256(pdf).hexdigest(),
             'html_sha256': hashlib.sha256(html).hexdigest()}
    # Bind retries to the exact content, recipients, approval and note.
    key = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    value.update(pdf_base64=base64.b64encode(pdf).decode(), html_base64=base64.b64encode(html).decode())
    return value, key


def credential(path):
    source = Path(path)
    mode = source.stat().st_mode
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise ValueError('Service credential must be a private regular file')
    token = source.read_text().strip()
    if not token or any(c.isspace() for c in token):
        raise ValueError('Invalid service credential')
    return token


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=['founders', 'customer'], required=True)
    for name in ['pdf', 'html', 'target', 'run-date', 'note']:
        p.add_argument('--' + name, required=True)
    p.add_argument('--recipient', action='append', default=[])
    p.add_argument('--approval-ref')
    p.add_argument('--base-url', default=os.getenv('BACKBOND_SCANNER_URL', 'https://api.backbond.ai'))
    p.add_argument('--credential-file', default=os.getenv('BACKBOND_REPORT_MAIL_CREDENTIAL'))
    args = p.parse_args(argv)
    try:
        url = urlsplit(args.base_url)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in {'', '/'}):
            raise ValueError('Scanner URL must be an HTTPS origin')
        payload, key = build_payload(args)
        if not args.credential_file:
            raise ValueError('Provide the sealed BAC service credential file')
        token = credential(args.credential_file)
        with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
            response = client.post(args.base_url.rstrip('/') + '/scan/v1/reports/mail',
                headers={'Authorization': 'Bearer ' + token, 'Idempotency-Key': key}, json=payload)
        if response.status_code not in {200, 201, 202}:
            print('Report mail request refused (HTTP %d)' % response.status_code, file=sys.stderr)
            return 1
        result = response.json()
        # Do not echo provider response bodies, report HTML or credentials.
        receipt = {k: result[k] for k in ['request_id', 'state', 'message_id']
                   if isinstance(result.get(k), str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', result[k])}
        if not receipt:
            raise ValueError('Missing report mail receipt')
        print(json.dumps(receipt, sort_keys=True))
        return 0 if receipt.get('state') == 'accepted' else 2
    except (OSError, ValueError, TypeError, AttributeError, httpx.HTTPError):
        print('Report mail request failed; check inputs, credential placement and service availability', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
