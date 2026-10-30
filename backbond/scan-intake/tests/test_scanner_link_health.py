from test_operations_status import app_env
from fastapi.testclient import TestClient
import httpx
import pytest

@pytest.mark.parametrize('status,expected', [(422,200),(401,503),(403,503),(500,503),(200,503)])
def test_link_probe_is_empty_and_checks_auth_routing(app_env, monkeypatch, status, expected):
    app, *_ = app_env
    monkeypatch.setattr(app, '_scanner_health_contexts', lambda: [{'tenant_id': 'partner-test'}])
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
    monkeypatch.setattr(app, '_scanner_health_contexts', lambda: [{'tenant_id': 'partner-test'}])
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


def test_health_checks_all_active_lanes_after_auth_failure(app_env, monkeypatch):
    import json
    app, *_ = app_env
    contexts = [{'tenant_id': 'partner-expired'}, {'tenant_id': 'partner-valid'}]
    monkeypatch.setattr(app, '_scanner_health_contexts', lambda: contexts)
    monkeypatch.setattr(app, '_headers', lambda data: {'Authorization': data['tenant_id']})
    calls = []
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kw):
            calls.append(kw)
            return httpx.Response(401 if len(calls) == 1 else 422)
    monkeypatch.setattr(app.httpx, 'Client', Client)
    result = app.scanner_link_health()
    body = json.loads(result.body)
    assert result.status_code == 503
    assert body['required_lanes'] == body['checked_lanes'] == 2
    assert body['healthy_lanes'] == 1
    assert body['upstream_status'] == 401
    assert result.headers['cache-control'] == 'no-store'
    assert all(call['json'] == {} for call in calls)
    assert b'partner-' not in result.body


def test_health_contexts_follow_admissible_codes_not_stale_credentials(app_env, monkeypatch):
    from qa_service import Code
    app, *_ = app_env
    app.store.db.execute('DELETE FROM codes')
    for code, partner, expiry, active in [('GOOD', 'good', '2099-01-01', True),
                                          ('OLD', 'old', '2000-01-01', True),
                                          ('OFF', 'off', '2099-01-01', False)]:
        app.store.put_code(Code(code, partner, partner, 'standard', 10, 10, expiry, active))
    monkeypatch.setenv('QA_PARTNER_CODE_REQUIRED', '1')
    monkeypatch.setattr(app, '_tenant_tokens', lambda: {'partner-stale': 'secret'})
    assert app._scanner_health_contexts() == [{'tenant_id': 'partner-good'}]
    monkeypatch.setenv('QA_PARTNER_CODE_REQUIRED', '0')
    assert app._scanner_health_contexts() == [{'tenant_id': 'partner-good'}, {'tenant_id': 'partner-public'}]


def test_health_empty_and_timeout_fail_closed(app_env, monkeypatch):
    import json
    app, *_ = app_env
    monkeypatch.setattr(app, '_scanner_health_contexts', lambda: [])
    result = app.scanner_link_health()
    assert result.status_code == 503
    assert json.loads(result.body)['error_class'] == 'no_active_lanes'
    monkeypatch.setattr(app, '_scanner_health_contexts', lambda: [{}, {}])
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kw): pytest.fail('expired budget must not send')
    monkeypatch.setattr(app.httpx, 'Client', Client)
    ticks = iter([1, 7])
    monkeypatch.setattr(app.time, 'monotonic', lambda: next(ticks))
    result = app.scanner_link_health()
    body = json.loads(result.body)
    assert result.status_code == 503
    assert body['required_lanes'] == 2 and body['checked_lanes'] == 0
    assert body['error_class'] == 'timeout'
    assert result.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('replacement,expected,calls_expected', [(True, 200, 2), (False, 503, 1)])
def test_health_uses_real_check_replacement_path(app_env, monkeypatch, replacement, expected, calls_expected):
    import json
    app, *_ = app_env
    monkeypatch.setattr(app, '_scanner_health_contexts', lambda: [{'tenant_id': 'partner-test'}])
    selected = iter(['Bearer old-secret', 'Bearer new-secret' if replacement else 'Bearer old-secret'])
    monkeypatch.setattr(app, '_headers', lambda context: {'Authorization': next(selected)})
    calls = []
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kw):
            calls.append(kw)
            assert kw['json'] == {}
            assert 0 < kw['timeout'] <= 5
            return httpx.Response(422 if kw['headers']['Authorization'] == 'Bearer new-secret' else 401)
    monkeypatch.setattr(app.httpx, 'Client', Client)
    result = app.scanner_link_health()
    assert result.status_code == expected
    assert len(calls) == calls_expected
    assert json.loads(result.body)['healthy_lanes'] == int(replacement)
    assert b'secret' not in result.body
