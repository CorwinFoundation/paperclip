"""Offline exact policies; no protected live binding or network calls."""
import hashlib
import json

import pytest
from harness.controlled_target_policy import load_fixed_handler_transport
from harness.controlled_permit_loader import AdmissionClosed, fingerprint
from tests.test_controlled_permit_loader import PAYLOAD


def policy_file(tmp_path, **updates):
    folder = tmp_path / 'private'
    folder.mkdir(mode=0o700, exist_ok=True)
    policy = dict(version=1, kind='exact-fixed-handler-complete-request',
                  target_sha256=fingerprint(PAYLOAD), upper_microusd=123)
    policy.update(updates)
    raw = json.dumps(policy).encode()
    path = folder / 'policy.json'
    path.write_bytes(raw)
    path.chmod(0o600)
    return path, hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('price', [0, 123, 10_000_001, 2**63 - 1])
def test_exact_policy_loads_without_network_or_enabling_permit(tmp_path, price):
    # The Founder ceiling is retired; the exact service-owned price remains
    # finite, nonnegative and SQLite-representable. Zero is still zero.
    path, digest = policy_file(tmp_path, upper_microusd=price)
    target = load_fixed_handler_transport(path=path, expected_sha256=digest, payload=PAYLOAD)
    assert target._price.upper_microusd == price
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize('updates', [dict(kind='hostname-free'), dict(upper_microusd=None),
    dict(upper_microusd=True), dict(upper_microusd=-1), dict(upper_microusd=2**63),
    dict(upper_microusd=1.5),
    dict(target_sha256='0'*64), dict(version=True)])
def test_incomplete_or_wrong_policy_closed(tmp_path, updates):
    path, digest = policy_file(tmp_path, **updates)
    with pytest.raises(AdmissionClosed):
        load_fixed_handler_transport(path=path, expected_sha256=digest, payload=PAYLOAD)


@pytest.mark.parametrize('mutation', ['digest', 'missing', 'permissions', 'symlink', 'target', 'parent'])
def test_missing_or_changed_binding_closed(tmp_path, mutation):
    path, digest = policy_file(tmp_path)
    payload = PAYLOAD
    if mutation == 'digest':
        digest = '0'*64
    elif mutation == 'missing':
        path.unlink()
    elif mutation == 'permissions':
        path.chmod(0o644)
    elif mutation == 'symlink':
        other = path.with_name('other')
        path.rename(other)
        path.symlink_to(other)
    elif mutation == 'parent':
        path.parent.chmod(0o755)
    else:
        payload = dict(PAYLOAD, credential_secret='changed')
    with pytest.raises(AdmissionClosed):
        load_fixed_handler_transport(path=path, expected_sha256=digest, payload=payload)


@pytest.mark.parametrize('mutation', ['none', 'secret', 'endpoint', 'payload', 'permit'])
def test_service_configuration_denies_before_policy_io(tmp_path, monkeypatch, mutation):
    from harness import controlled_permit_loader as loader
    from harness import controlled_target_policy as policy
    from tests.test_controlled_target_transport import prepared
    from tests.test_controlled_permit_loader import context
    _, record, _ = prepared(tmp_path, monkeypatch, price=10)
    ctx = context(record)
    loader.bind_scanner_permit(ctx, scan_id='e'*32)
    permit = loader.load_bound_scanner_permit(ctx, scan_id='e'*32)
    config = dict(path='/missing', expected_sha256='0'*64)
    payload = PAYLOAD
    secret = PAYLOAD['credential_secret']
    endpoint = PAYLOAD['agent_endpoint']['url']
    if mutation == 'none': config = None
    elif mutation == 'secret': secret = 'changed'
    elif mutation == 'endpoint': endpoint += '/changed'
    elif mutation == 'payload': payload = dict(PAYLOAD, pack_id='changed')
    checked = []
    monkeypatch.setattr(policy, 'load_fixed_handler_transport', lambda **kw: checked.append(kw))
    try:
        with pytest.raises(AdmissionClosed):
            policy.load_bound_target_transport(config, permit=object() if mutation == 'permit' else permit,
                                               secret=secret, endpoint=endpoint, payload=payload)
        assert checked == []
    finally:
        permit.close()


def test_service_configuration_uses_exact_policy_and_binding(tmp_path, monkeypatch):
    from harness import controlled_permit_loader as loader
    from harness.controlled_target_policy import load_bound_target_transport
    from tests.test_controlled_target_transport import prepared
    from tests.test_controlled_permit_loader import context
    _, record, _ = prepared(tmp_path, monkeypatch, price=10)
    ctx = context(record)
    loader.bind_scanner_permit(ctx, scan_id='e'*32)
    permit = loader.load_bound_scanner_permit(ctx, scan_id='e'*32)
    path, digest = policy_file(tmp_path)
    try:
        target = load_bound_target_transport(dict(path=path, expected_sha256=digest),
            permit=permit, secret=PAYLOAD['credential_secret'], endpoint=PAYLOAD['agent_endpoint']['url'], payload=PAYLOAD)
        assert target._price.target_sha256 == permit.admission.binding['target']
    finally:
        permit.close()
