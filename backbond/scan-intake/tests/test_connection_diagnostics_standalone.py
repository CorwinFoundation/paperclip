"""Connection responses must work with only the deployed intake server files.

The real route and outbound response parser run against synthetic HTTP replies
inside an isolated Python process. No application startup, network, or database
is needed, and the repository's harness package is deliberately unavailable.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


SERVER = Path(__file__).parents[1] / 'server'

STANDALONE_CHECK = r'''
import ast
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace as NS

server = Path(__file__).parent
sys.path.insert(0, str(server))
assert importlib.util.find_spec('harness') is None
from connection_diagnostics import safe_excerpt

secret = 'fixture-long-private-credential'
assert secret[:8] not in safe_excerpt('x' * 195 + secret + '<script>', [secret])
redacted = safe_excerpt('<script>\nAuthorization: Bearer other-fixture-key\n', [secret])
assert '<script>' not in redacted and 'other-fixture-key' not in redacted
assert '[REDACTED]' in redacted

calls = []
response_body = {}

class Client:
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def post(self, url, **kwargs):
        calls.append(url)
        return NS(status_code=200, json=lambda: response_body)

class HTTPException(Exception):
    def __init__(self, status, detail): self.status_code = status

class JSONResponse:
    def __init__(self, status_code, content):
        self.status_code, self.content = status_code, content

class Body:
    code, email = 'FIXTURE', 'fixture@example.test'
    def model_dump(self):
        return {'code': self.code, 'email': self.email,
                'credential_secret': secret, 'agent_endpoint': {'transport': 'rest'}}

env = {
    '__name__': 'standalone_intake_fixture', 'os': NS(environ={'QA_READY': '1'}),
    'httpx': NS(Client=Client), 're': re, 'logging': logging,
    'SCANNER_URL': 'https://scanner.invalid/scanner/v1',
    '_headers': lambda data: {}, 'controlled_operator': None,
    '_resolve_code': lambda code: {'code': code, 'partner': 'fixture'},
    'check_gate': NS(allow=lambda **kwargs: True), '_client': lambda request: 'fixture',
    '_payload': lambda clean: dict(clean), '_link_error': lambda exc: type(exc).__name__,
    'proofs': NS(issue=lambda fields: 'fixture-proof', ttl=60),
    'HTTPException': HTTPException, 'JSONResponse': JSONResponse,
}
tree = ast.parse((server / 'app.py').read_text(encoding='utf-8'))
for name in ('_scanner_check', 'connection_check'):
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    fn.decorator_list, fn.returns = [], None
    for arg in fn.args.args:
        arg.annotation = None
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'app.py', 'exec'), env)

response_body = {'success': True}
result = env['connection_check'](Body(), NS(headers={}))
assert result == {'ok': True, 'connection_proof': 'fixture-proof', 'expires_in': 60}, result
assert len(calls) == 1

response_body = {'success': False, 'error': 'HTTP 401', 'failed_stage': 'auth',
    'target_status': 401, 'elapsed_ms': 12,
    'response_excerpt': '<script>\nAuthorization: Bearer ' + secret}
result = env['connection_check'](Body(), NS(headers={}))
assert result['ok'] is False and 'connection_proof' not in result
assert result['target_status'] == 401 and result['failed_stage'] == 'auth'
assert secret not in result['response_excerpt'] and '<script>' not in result['response_excerpt']
assert '[REDACTED]' in result['response_excerpt'] and len(calls) == 2
print(json.dumps({'standalone_success_proof': True, 'failure_diagnostics_redacted': True,
    'synthetic_http_calls': len(calls), 'real_network_calls': 0, 'harness_available': False}))
'''


class StandaloneConnectionDiagnosticsTests(unittest.TestCase):
    def test_public_route_handles_success_and_redacted_failure_without_scanner_package(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder)
            for name in ('app.py', 'connection_diagnostics.py'):
                shutil.copyfile(SERVER / name, stage / name)
            check = stage / 'check.py'
            check.write_text(STANDALONE_CHECK, encoding='utf-8')
            env = os.environ.copy()
            env.pop('PYTHONPATH', None)
            result = subprocess.run([sys.executable, '-I', str(check)], cwd=stage,
                                    env=env, capture_output=True, text=True,
                                    encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"harness_available": false', result.stdout)
            self.assertIn('"real_network_calls": 0', result.stdout)


if __name__ == '__main__':
    unittest.main()
