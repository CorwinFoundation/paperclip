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
    monkeypatch.setattr(app, '_headers', lambda: {})
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
    monkeypatch.setattr(app.httpx, 'Client', lambda **kw: (_ for _ in ()).throw(error))
    result = app.scanner_link_health()
    assert result.status_code == 503
    assert category.encode() in result.body and b'secret' not in result.body
