"""Connection auth replacement is bounded and never reuses a rejected key."""
import ast
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx

SOURCE = Path(__file__).resolve().parents[1] / 'backbond/scan-intake/server/app.py'


def invoke(statuses, tokens, caplog):
    tree = ast.parse(SOURCE.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_post_connection_check')
    selected = iter(tokens)
    namespace = {'_headers': lambda data: {'Authorization': next(selected)},
                 'SCANNER_URL': 'https://scanner.invalid', 'logging': logging}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    calls = []
    responses = iter(statuses)
    def post(url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=next(responses))
    with caplog.at_level(logging.WARNING):
        response = namespace['_post_connection_check'](SimpleNamespace(post=post), {'tenant_id': 'partner-test'})
    assert 'secret-' not in caplog.text
    assert 'scanner.invalid' not in caplog.text
    return response.status_code, calls


@pytest.mark.parametrize('status', [200, 403, 422, 500])
def test_other_responses_are_never_retried(status, caplog):
    result, calls = invoke([status], ['Bearer secret-old'], caplog)
    assert result == status
    assert len(calls) == 1


def test_rejected_unchanged_credential_fails_loudly(caplog):
    result, calls = invoke([401], ['Bearer secret-old', 'Bearer secret-old'], caplog)
    assert result == 401
    assert len(calls) == 1
    assert 'replacement_available=False' in caplog.text


@pytest.mark.parametrize('replacement_status', [200, 401])
def test_replacement_is_tried_exactly_once(replacement_status, caplog):
    result, calls = invoke([401, replacement_status], ['Bearer secret-old', 'Bearer secret-new'], caplog)
    assert result == replacement_status
    assert [c['headers']['Authorization'] for c in calls] == ['Bearer secret-old', 'Bearer secret-new']
    assert 'replacement_available=True' in caplog.text


@pytest.mark.parametrize('status,controlled', [(401, False), (401, True), (403, False), (403, True)])
def test_expected_permit_denial_is_distinct_from_scanner_authentication(status, controlled):
    tree = ast.parse(SOURCE.read_text())
    names = {'_scanner_check', '_link_error'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    response = httpx.Response(status, json={'detail': 'secret-upstream-text'},
                              request=httpx.Request('POST', 'https://scanner.invalid/connectivity-check'))
    calls = []
    class LocalHTTP:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    namespace = {'httpx': SimpleNamespace(**{name: getattr(httpx, name) for name in
        ('HTTPStatusError', 'TimeoutException', 'ConnectError', 'HTTPError')},
        Client=lambda **kwargs: LocalHTTP()), 're': re,
        '_post_connection_check': lambda client, data: calls.append(dict(data)) or response}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    payload = {'controlled_permit_id': 'inert-permit'} if controlled else {}
    if status == 403 and controlled:
        assert namespace['_scanner_check'](payload) == (False,
            'Connection check failed. Check the endpoint, authentication and response settings.')
    else:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            namespace['_scanner_check'](payload)
        assert namespace['_link_error'](caught.value) == 'authentication_error'
    assert calls == [payload]


@pytest.mark.parametrize('packet', ['{}', '{"token": ""}', 'invalid-json'])
def test_tenant_lane_does_not_read_unused_default_credential(tmp_path, monkeypatch, packet):
    # Restored production code chooses a mapped tenant before sending. A broken
    # unused default file must not prevent that same tenant reaching upstream.
    import os
    credential = tmp_path / 'default.json'
    credential.write_text(packet)
    monkeypatch.setenv('QA_SCANNER_CREDENTIAL_PATH', str(credential))
    tree = ast.parse(SOURCE.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
             and n.name in {'_headers', '_post_connection_check', '_link_error'}]
    namespace = {'os': os, 'json': __import__('json'), 'Path': Path,
                 '_tenant_tokens': lambda: {'partner-live': 'tenant-secret'},
                 'SCANNER_URL': 'https://scanner.invalid', 'logging': logging, 'httpx': httpx}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200)
    response = namespace['_post_connection_check'](SimpleNamespace(post=post),
                                                   {'tenant_id': 'partner-live'})
    assert response.status_code == 200
    assert calls[0]['headers'] == {'Authorization': 'Bearer tenant-secret'}
    # The default lane still fails closed, without a network call.
    with pytest.raises((KeyError, ValueError)) as caught:
        namespace['_post_connection_check'](SimpleNamespace(post=post), {'tenant_id': 'partner-other'})
    assert namespace['_link_error'](caught.value) in {'local_config_error', 'invalid_response_or_config'}
    assert len(calls) == 1
