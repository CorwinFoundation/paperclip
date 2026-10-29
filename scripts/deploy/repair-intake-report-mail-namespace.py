#!/usr/bin/env python3
"""Idempotent AriClaw repair for BEAAA-44392; never reads credential contents."""
import ast
import os
import re
from pathlib import Path
import subprocess


def replace_function(text, name, replacement):
    node = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = text.splitlines(keepends=True)
    return ''.join(lines[:node.lineno - 1]) + replacement + '\n' + ''.join(lines[node.end_lineno:])


def repair():
    if os.geteuid() != 0:
        raise SystemExit('run as root on AriClaw')
    root = Path(__file__).resolve().parents[2]
    maintained = (root / 'backbond/scan-intake/server/app.py').read_text()
    node = next(n for n in ast.parse(maintained).body if isinstance(n, ast.FunctionDef) and n.name == '_report_mail_gateway')
    replacement = ast.get_source_segment(maintained, node)
    active = Path('/opt/backbond/scan-intake/server/app.py')
    text = active.read_text()
    updated = replace_function(text, '_report_mail_gateway', replacement)
    updated = updated.replace("logging.getLogger(__name__).error('scanner_link_failure category=%s', category)",
        "logging.getLogger(__name__).error('scanner_link_failure category=%s upstream_status=%s operation=connectivity-check', category, exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None)")
    ast.parse(updated)
    configs = [Path('/etc/systemd/system/backbond-scan-intake.service.d/report-mail-grant.conf'),
               Path('/etc/backbond/report-mail.env')]
    # Only remove the collision from the report-mail configuration. Existing edge
    # settings in scan-intake.env remain authoritative; no default path is invented.
    for config in configs:
        old = config.read_text()
        new = re.sub(r'(?<![A-Za-z0-9_])ARE_KEY_STORE_PATH=', 'QA_REPORT_MAIL_KEY_STORE_PATH=', old)
        # Restore only the temporarily disabled report-mail store assignment.
        # Other commented settings stay disabled.
        new = re.sub(r'(?m)^(\s*)#\s*(Environment=["\']?QA_REPORT_MAIL_KEY_STORE_PATH=|QA_REPORT_MAIL_KEY_STORE_PATH=)',
                     r'\1\2', new)
        if new != old:
            backup = config.with_name(config.name + '.before-beaaa44392')
            if not backup.exists():
                backup.write_text(old)
                backup.chmod(0o600)
            config.write_text(new)
    if updated != text:
        backup = active.with_name('app.py.before-beaaa44392')
        if not backup.exists():
            backup.write_text(text)
        active.write_text(updated)
    # The retired review service must not contend with intake's public port.
    directory = Path('/etc/systemd/system/backbond-qa-review.service.d')
    directory.mkdir(parents=True, exist_ok=True)
    (directory / '99-separate-port.conf').write_text('[Service]\nExecStart=\nExecStart=/opt/backbond/scan-venv/bin/uvicorn app:app --host 127.0.0.1 --port 8795 --no-access-log\n')
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'restart', 'backbond-scan-intake'], check=True)
    print('Intake restarted; report-mail store isolated; retired review port set to 8795.')
    # The caller can repeat this read after the service has finished starting.
    print('Verify: curl -fsS https://api.backbond.ai/scan/v1/scanner-link-health (ok must be true)')


if __name__ == '__main__':
    repair()
