from test_operations_status import app_env
from fastapi.testclient import TestClient
import httpx
import pytest

@pytest.mark.parametrize('status,expected', [(422,200),(401,503),(403,503),(500,503),(200,503)])
def test_link_probe_is_empty_and_checks_auth_routing(app_env, monkeypatch, status, expected):
    app, *_ = app_env
    calls = []
    class Client:
        def __init__(self, **kw): assert kw['timeout'] == 5
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kw):
            calls.append(kw)
            return httpx.Response(status)
    monkeypatch.setattr(app, '_tenant_tokens', lambda: {})
    monkeypatch.setattr(app, '_headers', lambda data=None: {})
    monkeypatch.setattr(app.httpx, 'Client', Client)
    result = app.scanner_link_health()
    assert result.status_code == expected
    assert calls[0]['json'] == {}
    assert b'endpoint' not in result.body and b'Authorization' not in result.body

@pytest.mark.parametrize('error,category', [(httpx.ReadTimeout('secret'), 'timeout'),
    (httpx.ConnectError('secret'), 'connect_error'), (PermissionError('secret'), 'local_config_error'),
    (ValueError('secret'), 'invalid_response_or_config')])
def test_link_failure_is_sanitized(app_env, monkeypatch, error, category):
    app, *_ = app_env
    monkeypatch.setattr(app, '_tenant_tokens', lambda: {})
    monkeypatch.setattr(app.httpx, 'Client', lambda **kw: (_ for _ in ()).throw(error))
    result = app.scanner_link_health()
    assert result.status_code == 503
    assert category.encode() in result.body and b'secret' not in result.body

@pytest.mark.parametrize('status,category', [(401, 'authentication_error'),
    (403, 'authentication_error'), (422, 'upstream_client_error'), (500, 'upstream_server_error')])
def test_check_upstream_http_failure(app_env, monkeypatch, status, category):
    app, *_ = app_env
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kw):
            return httpx.Response(status, request=httpx.Request('POST', url), text='secret')
    monkeypatch.setattr(app, '_headers', lambda data: {})
    monkeypatch.setattr(app.httpx, 'Client', Client)
    with pytest.raises(httpx.HTTPStatusError) as caught:
        app._scanner_check({})
    assert app._link_error(caught.value) == category
    assert 'secret' not in app._link_error(caught.value)


def test_report_mail_store_does_not_override_edge_store(app_env, monkeypatch):
    app, *_ = app_env
    import sys
    from types import SimpleNamespace
    captured = []
    monkeypatch.setitem(sys.modules, 'edge_gateway', SimpleNamespace(
        EdgeGateway=SimpleNamespace(from_env=lambda env: captured.append(env) or object())))
    monkeypatch.setenv('ARE_KEY_STORE_PATH', '/edge-store')
    monkeypatch.delenv('QA_REPORT_MAIL_KEY_STORE_PATH', raising=False)
    assert app._report_mail_gateway() is None
    monkeypatch.setenv('QA_REPORT_MAIL_KEY_STORE_PATH', '/mail-store')
    assert app._report_mail_gateway() is not None
    assert captured[0]['ARE_KEY_STORE_PATH'] == '/mail-store'
    assert app.os.environ['ARE_KEY_STORE_PATH'] == '/edge-store'


@pytest.mark.parametrize('valid,status', [(True, 200), (False, 503)])
def test_health_and_real_check_use_same_partner_bearer(app_env, monkeypatch, valid, status):
    app, *_ = app_env
    monkeypatch.setenv('QA_SCANNER_TOKEN', 'unused-invalid-default')
    monkeypatch.delenv('QA_SCANNER_CREDENTIAL_PATH', raising=False)
    monkeypatch.setattr(app, '_tenant_tokens', lambda: {'partner-test': 'selected-secret'})
    calls = []
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kw):
            calls.append(kw['headers'])
            assert kw['headers'] == {'Authorization': 'Bearer selected-secret'}
            code = (422 if not kw['json'] else 200) if valid else 401
            return httpx.Response(code, json={'success': True}, request=httpx.Request('POST', url))
    monkeypatch.setattr(app.httpx, 'Client', Client)
    health = app.scanner_link_health()
    assert health.status_code == status
    if valid:
        assert app._scanner_check({'tenant_id': 'partner-test'})[0]
    else:
        with pytest.raises(httpx.HTTPStatusError):
            app._scanner_check({'tenant_id': 'partner-test'})
    assert calls[0] == calls[1]
    assert b'secret' not in health.body and b'partner-test' not in health.body
