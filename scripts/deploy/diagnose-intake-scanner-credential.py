#!/usr/bin/env python3
"""Read-only AriClaw intake bearer fingerprints. Run as root; prints no tokens.

Compare credential_sha256 with scanner-deploy diagnostics scanner-auth output.
Uses the running service environment and systemd credential, not a guessed env
file. No target requests, key issuance, file writes or service restarts.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess


def selected_credentials(env, read):
    mapping = json.loads(env.get('QA_SCANNER_TOKENS_JSON', '{}'))
    directory = env.get('CREDENTIALS_DIRECTORY')
    if directory:
        packet = read(Path(directory) / 'qa-scanner-tokens.json')
        if packet is not None:
            mapping = json.loads(packet)
    dynamic = read(Path('/var/lib/backbond-qa/partner-code-tenants.json'))
    if dynamic is not None:
        for tenant, token in json.loads(dynamic).items():
            mapping.setdefault(tenant, token)
    token = env.get('QA_SCANNER_TOKEN', '')
    credential_path = env.get('QA_SCANNER_CREDENTIAL_PATH')
    if credential_path:
        token = json.loads(read(Path(credential_path)))['token']
    chosen = {'default': token, **mapping}
    return [{'selection': tenant, 'configured': bool(token),
             'credential_sha256': hashlib.sha256(token.encode()).hexdigest() if token else None}
            for tenant, token in chosen.items()]


def main():
    if os.geteuid() != 0:
        raise SystemExit('run as root on AriClaw')
    pid = int(subprocess.check_output(['systemctl', 'show', 'backbond-scan-intake',
                                      '--property=MainPID', '--value'], text=True))
    if pid <= 1:
        raise SystemExit('intake service has no running process')
    raw = Path(f'/proc/{pid}/environ').read_bytes()
    env = dict(row.decode().split('=', 1) for row in raw.split(b'\0') if b'=' in row)
    def read(path):
        try:
            return path.read_text()
        except FileNotFoundError:
            return None
    print(json.dumps({'credentials': selected_credentials(env, read)}, sort_keys=True))


if __name__ == '__main__':
    main()
