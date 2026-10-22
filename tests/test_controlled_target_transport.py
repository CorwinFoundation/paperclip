"""Offline transport bridge accounting, using the existing real permit fixture."""
from tests.test_controlled_permit_loader import bind_cases
import sqlite3
from unittest.mock import Mock

import pytest
from harness import controlled_permit_loader as loader
from harness import controlled_target_transport as transport
from tests.test_controlled_permit_loader import PAYLOAD, setup_state, arm_fixture, context


def prepared(tmp_path, monkeypatch, price=4):
    path, record = setup_state(tmp_path, monkeypatch)
    arm_fixture(path)
    target = transport.ControlledTargetTransport(payload=PAYLOAD, price=transport.FixedRequestPrice(
        loader.fingerprint(PAYLOAD), price))
    return path, record, target


def test_preflight_target_judge_share_budget_before_io(tmp_path, monkeypatch):
    path, record, target = prepared(tmp_path, monkeypatch, 4_000_000)
    def io(self, prompt):
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT reserved_microusd FROM controlled_admission').fetchone()[0] in (4_000_000, 8_000_000)
        return 'reply'
    monkeypatch.setattr(transport._CustomerAgentHttpRunner, 'complete', io)
    assert target.preflight(record['permit_id'], tenant_id='tenant', payload=PAYLOAD, prompt='check') == 'reply'
    ctx = context(record)
    loader.bind_scanner_permit(ctx, scan_id='a'*32)
    permit = loader.load_bound_scanner_permit(ctx, scan_id='a'*32)
    cases = bind_cases(permit, tmp_path, monkeypatch)
    try:
        assert target.complete(permit, test_id=cases[0].case_id, phase='target', prompt='test') == 'reply'
        with pytest.raises(loader.AdmissionClosed):
            permit.call(test_id=cases[0].case_id, phase='judge', upper_microusd=3_000_000, operation=lambda: pytest.fail('over budget'))
        with pytest.raises(loader.AdmissionClosed):
            target.complete(permit, test_id=cases[0].case_id, phase='target', prompt='replay')
    finally:
        permit.close()


def test_lost_target_response_stays_consumed_after_restart(tmp_path, monkeypatch):
    path, record, target = prepared(tmp_path, monkeypatch)
    ctx = context(record)
    loader.bind_scanner_permit(ctx, scan_id='b'*32)
    io = Mock(side_effect=TimeoutError('unknown'))
    monkeypatch.setattr(transport._CustomerAgentHttpRunner, 'complete', io)
    permit = loader.load_bound_scanner_permit(ctx, scan_id='b'*32)
    cases = bind_cases(permit, tmp_path, monkeypatch)
    with pytest.raises(TimeoutError):
        target.complete(permit, test_id=cases[0].case_id, phase='target', prompt='test')
    permit.close()
    permit = loader.load_bound_scanner_permit(ctx, scan_id='b'*32)
    cases = bind_cases(permit, tmp_path, monkeypatch)
    try:
        with pytest.raises(loader.AdmissionClosed):
            target.complete(permit, test_id=cases[0].case_id, phase='target', prompt='test')
        assert io.call_count == 1
    finally:
        permit.close()


@pytest.mark.parametrize('cost', [None, True, -1, 10_000_001, 1.5])
def test_unknown_or_invalid_price_closed_before_transport(cost):
    with pytest.raises(loader.AdmissionClosed):
        transport.ControlledTargetTransport(payload=PAYLOAD, price=transport.FixedRequestPrice(loader.fingerprint(PAYLOAD), cost))


def test_alternate_protocol_and_changed_target_closed():
    for payload in [dict(PAYLOAD, agent_endpoint={'url':'https://fixture.invalid','transport':'openai_chat'}),
                    dict(PAYLOAD, credential_secret='changed')]:
        with pytest.raises(loader.AdmissionClosed):
            transport.ControlledTargetTransport(payload=payload, price=transport.FixedRequestPrice(loader.fingerprint(PAYLOAD), 1))


@pytest.mark.parametrize('outcome', ['success', 'redirect', 'http_error', 'timeout'])
def test_real_runner_single_http_attempt_and_durable_replay_denial(tmp_path, monkeypatch, outcome):
    """Exercise base runner serialization/streaming; replace only the network."""
    import httpx
    import json
    from harness.customer_runner import ScopedEgressAllowlist
    from harness.runner import RunnerError

    path, record, target = prepared(tmp_path, monkeypatch, 1_000_000)
    ctx = context(record)
    scan_id = 'c' * 32
    loader.bind_scanner_permit(ctx, scan_id=scan_id)
    sent = []

    def send(request):
        # This is the actual HTTP boundary, after runner serialization.
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT reserved_microusd FROM controlled_admission').fetchone() == (1_000_000,)
            assert db.execute('SELECT state FROM controlled_paid_calls').fetchone() == ('call_unknown',)
        assert str(request.url) == PAYLOAD['agent_endpoint']['url']
        assert request.headers['authorization'] == 'Bearer ' + PAYLOAD['credential_secret']
        assert json.loads(request.content) == {'prompt': 'bounded prompt'}
        sent.append(request)
        if outcome == 'timeout':
            raise httpx.ReadTimeout('offline timeout', request=request)
        status = {'success': 200, 'redirect': 307, 'http_error': 503}[outcome]
        return httpx.Response(status, headers={'location': 'https://fixture.invalid/second'},
                              stream=httpx.ByteStream(b'{"response":"reply"}'))

    def client(scope, **kwargs):
        assert kwargs['follow_redirects'] is False
        return httpx.Client(transport=httpx.MockTransport(send), **kwargs)

    monkeypatch.setattr(ScopedEgressAllowlist, 'client', client)
    permit = loader.load_bound_scanner_permit(ctx, scan_id=scan_id)
    cases = bind_cases(permit, tmp_path, monkeypatch)
    try:
        if outcome == 'success':
            assert target.complete(permit, test_id=cases[0].case_id, phase='target', prompt='bounded prompt') == 'reply'
        else:
            with pytest.raises(RunnerError):
                target.complete(permit, test_id=cases[0].case_id, phase='target', prompt='bounded prompt')
    finally:
        permit.close()
    assert len(sent) == 1
    # A new permit connection must neither resend nor refund any outcome.
    permit = loader.load_bound_scanner_permit(ctx, scan_id=scan_id)
    cases = bind_cases(permit, tmp_path, monkeypatch)
    try:
        with pytest.raises(loader.AdmissionClosed):
            target.complete(permit, test_id=cases[0].case_id, phase='target', prompt='bounded prompt')
        assert permit.admission.ledger.db.execute(
            'SELECT reserved_microusd FROM controlled_admission').fetchone() == (1_000_000,)
    finally:
        permit.close()
    assert len(sent) == 1

@pytest.mark.parametrize('outcome', ['success', 'timeout'])
def test_connectivity_handler_meters_and_denies_replay(tmp_path, monkeypatch, outcome):
    from api.scan_handlers import connectivity_check_handler, HandlerError
    from harness.runner import RunnerError
    path, record, target = prepared(tmp_path, monkeypatch, 10_000_000)
    calls = []
    def io(self, prompt):
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT reserved_microusd FROM controlled_admission').fetchone() == (10_000_000,)
            assert db.execute('SELECT state FROM controlled_preflight').fetchone() == ('check_unknown',)
        calls.append(prompt)
        if outcome == 'timeout':
            raise RunnerError('unknown')
        return 'reply'
    monkeypatch.setattr(transport._CustomerAgentHttpRunner, 'complete', io)
    body = dict(PAYLOAD, controlled_permit_id=record['permit_id'])
    status, result = connectivity_check_handler(body, tenant_id='tenant', controlled_transport=target,
        runner_factory=lambda *a, **kw: pytest.fail('unmetered runner'))
    assert status == 200
    assert result['success'] == (outcome == 'success')
    with pytest.raises(HandlerError) as error:
        connectivity_check_handler(body, tenant_id='tenant', controlled_transport=target)
    assert error.value.status == 403
    assert len(calls) == 1
    with pytest.raises(HandlerError) as error:
        connectivity_check_handler(body, tenant_id='tenant')
    assert error.value.status == 503


def test_handler_check_and_100_canonical_cases_share_ten_dollars(tmp_path, monkeypatch):
    from api.scan_handlers import connectivity_check_handler
    path, record, target = prepared(tmp_path, monkeypatch, 100_000)
    calls = []
    monkeypatch.setattr(transport._CustomerAgentHttpRunner, 'complete',
                        lambda self, prompt: calls.append(prompt) or 'reply')
    body = dict(PAYLOAD, controlled_permit_id=record['permit_id'])
    assert connectivity_check_handler(body, tenant_id='tenant', controlled_transport=target)[1]['success']
    ctx = context(record)
    loader.bind_scanner_permit(ctx, scan_id='d'*32)
    permit = loader.load_bound_scanner_permit(ctx, scan_id='d'*32)
    cases = bind_cases(permit, tmp_path, monkeypatch)
    try:
        for case in cases[:99]:
            target.complete(permit, test_id=case.case_id, phase='target', prompt='test')
        with pytest.raises(loader.AdmissionClosed):
            target.complete(permit, test_id=cases[99].case_id,
                            phase='target', prompt='over budget')
        assert len(calls) == 100
        assert permit.admission.ledger.db.execute(
            'SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (100, 10_000_000)
    finally:
        permit.close()
