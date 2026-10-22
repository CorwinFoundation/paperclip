"""Exercise proposed install/rollback commands in separate offline processes."""
from tests.test_controlled_permit_loader import bind_cases
import json
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from harness import controlled_permit_loader as loader


PAYLOAD = {'agent_endpoint': {'url': 'https://offline.invalid'},
           'credential_secret': 'offline-fixture'}


def setup_state(tmp_path, monkeypatch):
    path = tmp_path / 'private' / 'permit.sqlite'
    record = dict(permit_id='p'*32, tenant_id='tenant', actor='operator',
                  request_id='request', target_sha256=loader.fingerprint(PAYLOAD),
                  token_sha256=hashlib.sha256(b'offline').hexdigest(),
                  expires_at=int(time.time())+300)
    loader.provision_disabled(path, record)
    monkeypatch.setenv('ARE_CONTROLLED_PERMIT_DB', str(path))
    return path, record


def context(record):
    return loader.validate_permit_context(record['permit_id'], tenant_id='tenant', payload=PAYLOAD)


def command(path, action, record=None):
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] /
                             'scripts/controlled_permit_state.py'),
         action, '--db', str(path)],
        input=json.dumps(record) if record is not None else '',
        text=True, capture_output=True, timeout=15)


def snapshot(path):
    with sqlite3.connect(path) as db:
        return '\n'.join(db.iterdump())


@pytest.mark.parametrize('consumed', [False, True])
def test_install_and_rollback_preserve_identity_and_unknown(tmp_path, monkeypatch, consumed):
    path, record = setup_state(tmp_path, monkeypatch)
    initial = snapshot(path)
    for _ in range(2):
        assert command(path, 'provision-disabled', record).returncode == 0
        assert snapshot(path) == initial
    changed = command(path, 'provision-disabled', dict(record, request_id='replacement'))
    assert changed.returncode != 0
    assert snapshot(path) == initial
    assert record['token_sha256'] not in changed.stderr

    assert command(path, 'arm-once', record).returncode == 0
    if consumed:
        descriptor = context(record)
        loader.bind_scanner_permit(descriptor, scan_id='offline-scan')
        permit = loader.load_bound_scanner_permit(descriptor, scan_id='offline-scan')
        cases = bind_cases(permit, tmp_path, monkeypatch)
        try:
            with pytest.raises(TimeoutError):
                permit.call(test_id=cases[0].case_id, phase='target', upper_microusd=123,
                            operation=lambda: (_ for _ in ()).throw(TimeoutError()))
        finally:
            permit.close()
    assert command(path, 'disable').returncode == 0
    disabled = snapshot(path)
    for action in ('disable', 'provision-disabled'):
        assert command(path, action, record).returncode == 0
        assert snapshot(path) == disabled
    assert command(path, 'arm-once', record).returncode != 0
    assert snapshot(path) == disabled
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT enabled,consumed,reserved_microusd,dispatch_state '
                          'FROM controlled_admission').fetchone() == (
            0, int(consumed), 123 if consumed else 0,
            'dispatch_unknown' if consumed else 'unclaimed')
    with pytest.raises(loader.AdmissionClosed):
        context(record)
