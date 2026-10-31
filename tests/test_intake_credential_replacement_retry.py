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


@pytest.mark.parametrize("broken_import", [False, True])
def test_systemd_only_deployment_processes_success_without_harness(tmp_path, broken_import):
    """Match Founder's 30 Oct reproduction: local module, 11 sealed mappings."""
    import json
    import shutil
    import subprocess
    import sys
    local_module = SOURCE.parent / 'connection_diagnostics.py'
    shutil.copyfile(local_module, tmp_path / local_module.name)
    mappings = {'synthetic-' + str(i): 'inert-' + str(i) for i in range(11)}
    (tmp_path / 'qa-scanner-tokens.json').write_text(json.dumps(mappings))
    script = r'''
import ast, builtins, json, logging, os, re, sys
from pathlib import Path
from types import SimpleNamespace
root, source = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root))
original_import = builtins.__import__
def deployed_import(name, *args, **kwargs):
    if name == 'harness' or name.startswith('harness.'):
        raise ModuleNotFoundError("No module named 'harness'")
    return original_import(name, *args, **kwargs)
builtins.__import__ = deployed_import
for key in ('QA_SCANNER_TOKEN', 'QA_SCANNER_TOKENS_JSON', 'QA_SCANNER_CREDENTIAL_PATH'):
    os.environ.pop(key, None)
os.environ['CREDENTIALS_DIRECTORY'] = str(root)
def deployed_path(value):
    return root / 'absent-dynamic.json' if str(value) == '/var/lib/backbond-qa/partner-code-tenants.json' else Path(value)
class Client:
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def post(self, url, *, json, headers):
        assert headers == {'Authorization': 'Bearer ' + mappings[json['tenant_id']]}
        return SimpleNamespace(status_code=200, json=lambda: {'success': True})
mappings = json.loads((root / 'qa-scanner-tokens.json').read_text())
names = {'_tenant_tokens', '_headers', '_post_connection_check', '_scanner_check'}
nodes = [n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name in names]
namespace = dict(os=os, json=json, Path=deployed_path, logging=logging, re=re,
                 httpx=SimpleNamespace(Client=Client), SCANNER_URL='https://scanner.invalid')
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
for tenant in mappings:
    assert namespace['_scanner_check']({'tenant_id': tenant})[0] is True
print('11 systemd-only mappings processed upstream success')
'''
    source = SOURCE
    if broken_import:
        source = tmp_path / 'app-broken.py'
        source.write_text(SOURCE.read_text().replace(
            'from connection_diagnostics import safe_excerpt',
            'from harness.connection_diagnostics import safe_excerpt'))
    result = subprocess.run([sys.executable, '-I', '-c', script, str(tmp_path), str(source)],
                            text=True, capture_output=True, timeout=20)
    if broken_import:
        assert result.returncode != 0
        assert "ModuleNotFoundError: No module named 'harness'" in result.stderr
        assert 'from harness.connection_diagnostics import safe_excerpt' in result.stderr
        return
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '11 systemd-only mappings processed upstream success'
