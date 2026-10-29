#!/usr/bin/env python3
"""Install the root-to-root BAC report-mail packet on AriClaw."""
import json
import math
import os
from pathlib import Path
import sys
import time


def install(source):
    if os.geteuid() != 0:
        raise ValueError('run as root on AriClaw')
    packet = json.loads(Path(source).read_text())
    if (packet['tenant_id'] != '81b0c474-2b92-4397-99e6-995e6e5e85ea'
            or packet['key_id'] != 'bac-report-mail-beaaa44023'
            or not math.isfinite(packet['expires_at'])
            or not time.time() < packet['expires_at']):
        raise ValueError('wrong or expired report-mail grant')
    record = packet['key_store']['keys'][packet['bearer']]
    if (record['scopes'] != ['POST /scan/v1/reports/mail']
            or record['tenant_id'] != packet['tenant_id']
            or record['key_id'] != packet['key_id']
            or record['expires_at'] != packet['expires_at'] or record['status'] != 'active'):
        raise ValueError('mismatched report-mail grant')
    os.umask(0o077)
    dest = Path('/etc/backbond/report-mail-grant.json')
    dest.write_text(json.dumps(packet['key_store']) + '\n')
    dest.chmod(0o600)
    directory = Path('/etc/systemd/system/backbond-scan-intake.service.d')
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'report-mail-grant.conf').write_text(
        '[Service]\nLoadCredential=report-mail-grant.json:/etc/backbond/report-mail-grant.json\n'
        'Environment="ARE_KEY_STORE_PATH=%d/report-mail-grant.json"\n'
        'Environment="QA_REPORT_MAIL_ENABLED=1"\n'
        'Environment="QA_REPORT_MAIL_TENANT_ID=81b0c474-2b92-4397-99e6-995e6e5e85ea"\n'
        'Environment="QA_REPORT_MAIL_KEY_ID=bac-report-mail-beaaa44023"\n'
        'Environment="QA_REPORT_MAIL_FOUNDERS=info@backbond.ai"\n')
    # Environment files override Environment=; append this file after existing files.
    Path('/etc/backbond/report-mail.env').write_text(
        'ARE_KEY_STORE_PATH=/run/credentials/backbond-scan-intake.service/report-mail-grant.json\n'
        'QA_REPORT_MAIL_ENABLED=1\n'
        'QA_REPORT_MAIL_TENANT_ID=81b0c474-2b92-4397-99e6-995e6e5e85ea\n'
        'QA_REPORT_MAIL_KEY_ID=bac-report-mail-beaaa44023\n'
        'QA_REPORT_MAIL_FOUNDERS=info@backbond.ai\n')
    with (directory / 'report-mail-grant.conf').open('a') as config:
        config.write('EnvironmentFile=/etc/backbond/report-mail.env\n')
    print('Installed /etc/backbond/report-mail-grant.json; systemd credential configured')


if __name__ == '__main__':
    install(sys.argv[1])
