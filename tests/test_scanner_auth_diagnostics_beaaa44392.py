"""Read-only key diagnostics must never disclose token-indexed store contents."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

HELPER = Path(__file__).resolve().parents[1] / 'scripts/deploy/scanner-deploy.sh'
PROGRAM = HELPER.read_text().split("<<'PY_AUTH'\n", 1)[1].split('\nPY_AUTH', 1)[0]


def run_program(env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith('ARE_')}
    result = subprocess.run(['python3', '-c', PROGRAM], env={**clean, **env}, capture_output=True, text=True, check=True)
    return result.stdout


def test_persistent_store_redacts_tokens_and_reports_expiry(tmp_path):
    store = tmp_path / 'keys.json'
    store.write_text(json.dumps({'keys': {'secret-bearer': {'key_id': 'intake-1', 'tenant_id': 'intake', 'expires_at': 1, 'status': 'active', 'extra': 'secret-extra'}}}))
    before = store.read_bytes()
    output = run_program({'ARE_KEY_STORE_PATH': str(store), 'ARE_EDGE_GATEWAY': 'on'})
    assert 'secret' not in output
    assert json.loads(output)['keys'] == [{'credential_sha256': hashlib.sha256(b'secret-bearer').hexdigest(), 'key_id': 'intake-1', 'tenant_id': 'intake', 'active': True, 'expired': True}]
    assert store.read_bytes() == before


def test_unset_store_reports_bootstrap_without_reissuing():
    output = run_program({'ARE_API_AUTH_TOKENS_JSON': '{"secret-bearer":"intake"}'})
    assert 'secret-bearer' not in output
    assert json.loads(output)['bootstrap_tenants'] == ['intake']
    assert json.loads(output)['store_configured'] is False


def test_missing_store_is_not_created(tmp_path):
    store = tmp_path / 'missing.json'
    assert json.loads(run_program({'ARE_KEY_STORE_PATH': str(store)}))['store_exists'] is False
    assert not store.exists()
