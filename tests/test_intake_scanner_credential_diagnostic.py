import hashlib
import importlib.util
from pathlib import Path


def test_running_credential_precedence_and_redaction():
    path = Path(__file__).resolve().parents[1] / 'scripts/deploy/diagnose-intake-scanner-credential.py'
    spec = importlib.util.spec_from_file_location('diagnose', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    files = {'/run/credentials/intake/qa-scanner-tokens.json': '{"tenant":"sealed-secret"}',
             '/var/lib/backbond-qa/partner-code-tenants.json': '{"tenant":"stale-secret","other":"dynamic-secret"}'}
    rows = module.selected_credentials({'QA_SCANNER_TOKEN':'default-secret',
        'QA_SCANNER_TOKENS_JSON':'{"tenant":"env-secret"}',
        'CREDENTIALS_DIRECTORY':'/run/credentials/intake'}, lambda p:files.get(str(p)))
    indexed = {r['selection']:r for r in rows}
    assert indexed['tenant']['credential_sha256'] == hashlib.sha256(b'sealed-secret').hexdigest()
    assert indexed['other']['credential_sha256'] == hashlib.sha256(b'dynamic-secret').hexdigest()
    assert indexed['default']['credential_sha256'] == hashlib.sha256(b'default-secret').hexdigest()
    assert 'secret' not in str(rows)


def test_deployed_outbound_selection_is_independent_of_gateway_store(monkeypatch):
    """Execute the maintained selectors without importing the service or secrets."""
    import ast
    import json
    import os
    from types import SimpleNamespace

    source = Path(__file__).resolve().parents[1] / 'backbond/scan-intake/server/app.py'
    tree = ast.parse(source.read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {'_headers', '_tenant_tokens'}]
    assert len(functions) == 2
    namespace = {'os': os, 'json': json,
                 'Path': lambda value: SimpleNamespace(is_file=lambda: False)}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), namespace)
    monkeypatch.delenv('QA_SCANNER_CREDENTIAL_PATH', raising=False)
    monkeypatch.setenv('QA_SCANNER_TOKEN', 'synthetic-default')
    monkeypatch.setenv('QA_SCANNER_TOKENS_JSON', '{"tenant":"synthetic-tenant"}')
    monkeypatch.delenv('CREDENTIALS_DIRECTORY', raising=False)
    for path in [None, '/synthetic/report-mail.json', '/synthetic/intake.json']:
        if path is None:
            monkeypatch.delenv('ARE_KEY_STORE_PATH', raising=False)
        else:
            monkeypatch.setenv('ARE_KEY_STORE_PATH', path)
        monkeypatch.setenv('QA_REPORT_MAIL_KEY_STORE_PATH', '/synthetic/mail.json')
        assert namespace['_headers']() == {'Authorization': 'Bearer synthetic-default'}
        assert namespace['_headers']({'tenant_id': 'tenant'}) == {'Authorization': 'Bearer synthetic-tenant'}
    monkeypatch.delenv('QA_SCANNER_TOKEN')
    assert namespace['_headers']() == {}


def test_dedicated_scanner_credential_overrides_legacy_default_and_fails_closed(tmp_path, monkeypatch):
    import ast
    import json
    import os
    import pytest
    source = Path(__file__).resolve().parents[1] / 'backbond/scan-intake/server/app.py'
    tree = ast.parse(source.read_text())
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_headers']
    namespace = {'os': os, 'json': json, 'Path': Path,
                 '_tenant_tokens': lambda: {'partner': 'partner-secret'}}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), namespace)
    credential = tmp_path / 'intake.json'
    credential.write_text('{"token":"scan-secret","tenant_id":"intake"}')
    monkeypatch.setenv('QA_SCANNER_CREDENTIAL_PATH', str(credential))
    monkeypatch.setenv('QA_SCANNER_TOKEN', 'legacy-secret')
    assert namespace['_headers']() == {'Authorization': 'Bearer scan-secret'}
    assert namespace['_headers']({'tenant_id': 'partner'}) == {'Authorization': 'Bearer partner-secret'}
    credential.write_text('{"token":""}')
    with pytest.raises(ValueError):
        namespace['_headers']()
    credential.unlink()
    with pytest.raises(FileNotFoundError):
        namespace['_headers']()


def test_diagnostic_matches_dedicated_credential_selection():
    path = Path(__file__).resolve().parents[1] / 'scripts/deploy/diagnose-intake-scanner-credential.py'
    spec = importlib.util.spec_from_file_location('diagnose_file', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = module.selected_credentials(
        {'QA_SCANNER_TOKEN': 'old', 'QA_SCANNER_CREDENTIAL_PATH': '/intake.json'},
        lambda p: '{"token":"dedicated-secret"}' if str(p) == '/intake.json' else None)
    assert rows == [{'selection': 'default', 'configured': True,
                     'credential_sha256': hashlib.sha256(b'dedicated-secret').hexdigest()}]
    assert 'dedicated-secret' not in str(rows)
