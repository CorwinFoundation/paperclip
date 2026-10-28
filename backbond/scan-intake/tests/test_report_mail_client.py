import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('report_mail_client', Path(__file__).parents[1] / 'report_mail_client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def args(tmp_path, **updates):
    (tmp_path / 'r.pdf').write_bytes(b'%PDF-1.7\nexact bytes')
    (tmp_path / 'r.html').write_text('<html>report</html>')
    value = dict(mode='founders', recipient=[], approval_ref=None, target='Example',
                 run_date='2026-10-23', note='test', pdf=str(tmp_path / 'r.pdf'), html=str(tmp_path / 'r.html'))
    value.update(updates)
    return SimpleNamespace(**value)


def test_retry_key_binds_approval_recipients_and_artifacts(tmp_path):
    a = args(tmp_path, mode='customer', recipient=['a@example.com'], approval_ref='BAC-74')
    payload, key = client.build_payload(a)
    assert client.build_payload(a) == (payload, key)
    for field, value in [('note', 'changed'), ('approval_ref', 'BAC-75'), ('recipient', ['b@example.com'])]:
        old = getattr(a, field)
        setattr(a, field, value)
        assert client.build_payload(a)[1] != key
        setattr(a, field, old)
    Path(a.pdf).write_bytes(b'%PDF-1.7\nchanged')
    assert client.build_payload(a)[1] != key


@pytest.mark.parametrize('updates', [dict(mode='customer'), dict(recipient=['a@example.com']),
    dict(mode='customer', recipient=['a@example.com'], approval_ref='other-74'),
    dict(run_date='invalid'), dict(target='')])
def test_invalid_input(tmp_path, updates):
    with pytest.raises(ValueError):
        client.build_payload(args(tmp_path, **updates))


def test_credential_permissions(tmp_path):
    f = tmp_path / 'credential'
    f.write_text('sealed-service-token')
    f.chmod(0o644)
    with pytest.raises(ValueError):
        client.credential(f)
    f.chmod(0o600)
    assert client.credential(f) == 'sealed-service-token'


def test_insecure_url_never_sends(monkeypatch, capsys):
    monkeypatch.setattr(client.httpx, 'Client', lambda **kw: pytest.fail('must not send'))
    assert client.main(['--mode', 'founders', '--pdf', 'x', '--html', 'y', '--target', 'Example',
                        '--run-date', '2026-10-23', '--note', 'test', '--base-url', 'http://example.com']) == 1
