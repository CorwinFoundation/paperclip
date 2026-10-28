import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('report_mail_authorization',
    Path(__file__).parents[1] / 'server' / 'report_mail_authorization.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def check(**changes):
    fields = dict(key_id='bac-key', tenant_id='bac-tenant', status='active',
                  expires_at=200.0, scopes=(policy.REPORT_MAIL_SCOPE,))
    fields.update(changes)
    record = SimpleNamespace(**fields)
    gateway = SimpleNamespace(resolve_record=lambda token: record if token == 'sealed' else None)
    return policy.authorize_report_mail(gateway, 'Bearer sealed', tenant_id='bac-tenant',
                                         key_id='bac-key', now=100)


def test_exact_configured_identity():
    assert check().key_id == 'bac-key'


@pytest.mark.parametrize('changes', [dict(key_id='founder-key'), dict(tenant_id='other'),
    dict(status='revoked'), dict(expires_at=100), dict(expires_at=float('inf')),
    dict(expires_at=float('nan')), dict(scopes=None), dict(scopes=()),
    dict(scopes=('POST /scanner/v1/scans',))])
def test_wrong_identity_expiry_or_scope_denied(changes):
    with pytest.raises(PermissionError):
        check(**changes)


@pytest.mark.parametrize('header', ['', 'Basic sealed', 'Bearer', 'Bearer sealed extra',
                                   'Bearer unknown', 'bearer sealed'])
def test_missing_or_invalid_bearer(header):
    gateway = SimpleNamespace(resolve_record=lambda token: None)
    with pytest.raises(PermissionError):
        policy.authorize_report_mail(gateway, header, tenant_id='bac-tenant', key_id='bac-key')
