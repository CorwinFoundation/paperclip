"""Bootstrap failure/replacement behavior for BEAAA-44392, without a restart."""
import pytest

from api.edge_gateway import AuthError, EdgeGateway, KeyStore


@pytest.mark.parametrize('failure', ['expiry', 'revocation'])
def test_bootstrap_replacement_reloads_without_restart(tmp_path, failure):
    now = [1000.0]
    clock = lambda: now[0]
    path = tmp_path / 'keys.json'
    env = {
        'ARE_EDGE_GATEWAY': 'on',
        'ARE_KEY_STORE_PATH': str(path),
        'ARE_API_AUTH_TOKENS_JSON': '{"old-token":"tenant-a"}',
        'ARE_KEY_STORE_BOOTSTRAP_TTL_SECONDS': '10',
    }
    gateway = EdgeGateway.from_env(env, clock=clock)
    assert gateway.resolve_tenant('old-token') == 'tenant-a'
    admin = KeyStore(path, clock=clock)
    if failure == 'expiry':
        now[0] += 11
    else:
        admin.revoke('bootstrap-0')
        now[0] += 6
    with pytest.raises(AuthError):
        gateway.resolve_tenant('old-token')

    # Reconstructing from unchanged env must not resurrect the failed key.
    restarted = EdgeGateway.from_env(env, clock=clock)
    with pytest.raises(AuthError):
        restarted.resolve_tenant('old-token')

    # Explicit replacement is visible to the original running gateway in <=5s.
    admin.issue(token='replacement-token', key_id='replacement', tenant_id='tenant-a',
                ttl_seconds=60, scopes=['connectivity:check'])
    now[0] += 6
    assert gateway.resolve_tenant('replacement-token') == 'tenant-a'
    with pytest.raises(AuthError):
        gateway.resolve_tenant('old-token')
    assert gateway.store.resolve('replacement-token').expires_at == now[0] + 54


def test_fileless_bootstrap_expires_without_writing_or_renewing(tmp_path):
    now = [1000.0]
    store = KeyStore(None, clock=lambda: now[0], bootstrap_env={'token': 'tenant-a'},
                     bootstrap_ttl_seconds=10)
    assert store.resolve('token') is not None
    now[0] += 11
    assert store.resolve('token') is None
    assert list(tmp_path.iterdir()) == []
