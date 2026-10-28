#!/usr/bin/env python3
"""Issue a 24-hour BAC report-mail grant to a root-owned 0600 plain file.

Run as root on srv1749248. Copy the file root-to-root to AriClaw.
Optional --recipient encrypts instead; default needs no GPG configuration.
Retries reuse the same active grant and original expiry.
Transfer this single script through the Founder workstation or AriClaw;
srv1749248 cannot fetch GitHub over outbound HTTPS or SSH.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time

TENANT = '81b0c474-2b92-4397-99e6-995e6e5e85ea'
KEY_ID = 'bac-report-mail-beaaa44023'
SCOPE = 'POST /scan/v1/reports/mail'
STORE = '/var/lib/are-scanner/edge/keys.json'


def issue_packet(store_path=STORE, now=None):
    from api.edge_gateway import KeyStore
    now = time.time() if now is None else now
    path = Path(store_path)
    if not path.is_file():
        raise ValueError('maintained issuer store is missing')
    store = KeyStore(path, clock=lambda: now)
    records = json.loads(path.read_text())['keys']
    matches = [(token, rec) for token, rec in records.items() if rec['key_id'] == KEY_ID]
    if len(matches) > 1:
        raise ValueError('duplicate report-mail key ID')
    if matches:
        token, rec = matches[0]
        if (rec['tenant_id'] != TENANT or rec['status'] != 'active'
                or rec['scopes'] != [SCOPE] or not now < rec['expires_at'] <= now + 86400):
            raise ValueError('existing report-mail grant mismatched, revoked or expired; explicit rotation required')
    else:
        token = secrets.token_urlsafe(48)
        store.issue(token=token, key_id=KEY_ID, tenant_id=TENANT, ttl_seconds=86400,
                    scopes=[SCOPE], issuer='Scanner-1749 root custodian',
                    custodian='AriClaw intake service', rotation_owner='Scanner-1749 root custodian',
                    revocation_owner='Scanner-1749 root custodian',
                    revocation_procedure='Revoke maintained issuer grant and refresh AriClaw store slice')
        os.chmod(path, 0o600)
        rec = json.loads(path.read_text())['keys'][token]
    return dict(schema='backbond.report-mail-handoff/v1', tenant_id=TENANT,
                key_id=KEY_ID, created_at=rec['created_at'], expires_at=rec['expires_at'],
                bearer=token, key_store={'version': 1, 'keys': {token: rec}})


def encrypt_packet(payload, target, recipient):
    fd, staging = tempfile.mkstemp(dir=target.parent, prefix='.encrypted-')
    os.close(fd)
    try:
        subprocess.run(['gpg', '--batch', '--yes', '--trust-model', 'always',
                        '--recipient', recipient, '--output', staging, '--encrypt'],
                       input=payload, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        os.replace(staging, target)
    finally:
        Path(staging).unlink(missing_ok=True)


def write_plain_packet(payload, target):
    fd, staging = tempfile.mkstemp(dir=target.parent, prefix='.grant-')
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(payload)
        os.chmod(staging, 0o600)
        os.replace(staging, target)
    finally:
        Path(staging).unlink(missing_ok=True)


def safe_diagnostic(value):
    """Redact bearer fields and generated tokens before logging diagnostics."""
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='replace')
    value = re.sub(r"(?i)(bearer[\"']?\s*[:=]?\s*[\"']?)[^\s\"',}]+", r"\1[REDACTED]", str(value))
    return re.sub(r'[A-Za-z0-9_-]{40,}', '[REDACTED]', value)


def report_failure(exc):
    # CalledProcessError.__str__ includes its command. Never log worker source
    # or stdout, which can contain the exported grant packet.
    message = (f'container command exited with status {exc.returncode}'
               if isinstance(exc, subprocess.CalledProcessError)
               else 'missing required field [REDACTED]' if isinstance(exc, KeyError)
               else safe_diagnostic(exc))
    print(f'Report-mail issuer/export failed: {type(exc).__name__}: {message}', file=sys.stderr)
    if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
        tail = '\n'.join(safe_diagnostic(exc.stderr).splitlines()[-12:])[-4096:]
        print(f'Container stderr tail:\n{tail}', file=sys.stderr)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--recipient', help='verified AriClaw GPG fingerprint already imported into root keyring')
    args = p.parse_args()
    if os.geteuid() != 0 or os.uname().nodename.split('.')[0] != 'srv1749248':
        p.error('run as root on srv1749248')
    if args.recipient and not re.fullmatch(r'[A-F0-9]{40}', args.recipient):
        p.error('full uppercase recipient fingerprint required')
    # Confirm recipient before issuance. GPG encrypts to this exact fingerprint.
    if args.recipient:
        subprocess.run(['gpg', '--batch', '--list-keys', args.recipient], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.umask(0o077)
    out = Path('/root/bac-report-mail-handoff')
    out.mkdir(mode=0o700, exist_ok=True)
    with (out / 'issuer.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        worker = (Path(__file__).read_text().rsplit("if __name__ == '__main__':", 1)[0]
                  + '\nif os.environ.get("ARE_KEY_STORE_PATH") != STORE: raise ValueError("maintained runtime store mismatch")\nprint(json.dumps(issue_packet()))\n')
        result = subprocess.run(['docker', 'exec', '-i', 'backbond-scanner-live-edge',
                                 'python', '-c', worker], check=True, capture_output=True)
        packet = json.loads(result.stdout)
        target = out / ('ariclaw-report-mail.gpg' if args.recipient else 'ariclaw-report-mail.json')
        if args.recipient:
            encrypt_packet(result.stdout, target, args.recipient)
        else:
            write_plain_packet(result.stdout, target)
        receipt = {k: packet[k] for k in ['tenant_id', 'key_id', 'created_at', 'expires_at']}
        receipt.update(output_path=str(target), recipient=args.recipient,
                       output_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        (out / 'receipt.json').write_text(json.dumps(receipt, sort_keys=True) + '\n')
        print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        report_failure(exc)
        sys.exit(1)
