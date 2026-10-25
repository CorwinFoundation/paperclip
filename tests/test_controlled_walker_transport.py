"""Controlled runs must not reach unmetered target or evaluator transports."""
from tests.test_controlled_permit_loader import bind_cases
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scanners import customer_agent_walker as walker


@pytest.mark.parametrize("pack_id", [None, walker.STARTER_PACK_V4_ID, walker.STARTER_PACK_V5_ID])
def test_controlled_walker_denies_before_any_transport(monkeypatch, pack_id):
    judge = Mock(side_effect=AssertionError("judge must not be constructed"))
    monkeypatch.setattr(walker, "judge_client_from_env", judge)
    runner = SimpleNamespace(
        controlled_permit=object(), pack_id=pack_id,
        complete=Mock(side_effect=AssertionError("target must not be called")),
        complete_conversation=Mock(side_effect=AssertionError("conversation must not be called")),
        case_progress_callback=Mock(),
    )
    with pytest.raises(walker.ProbeRunIncompleteError, match="controlled target paid-call transport"):
        list(walker.customer_agent_probe_run_fn(runner))
    runner.complete.assert_not_called()
    runner.complete_conversation.assert_not_called()
    runner.case_progress_callback.assert_not_called()
    judge.assert_not_called()


def test_controlled_walker_uses_saved_v4_form_and_metered_rest_and_judge(tmp_path, monkeypatch):
    import sqlite3
    from harness import controlled_permit_loader as loader
    from harness import controlled_target_transport as transport
    from tests.test_controlled_target_transport import prepared
    from tests.test_controlled_permit_loader import context

    path, record, target = prepared(tmp_path, monkeypatch, price=10)
    ctx = context(record)
    loader.bind_scanner_permit(ctx, scan_id='e'*32)
    permit = loader.load_bound_scanner_permit(ctx, scan_id='e'*32)
    cases = bind_cases(permit, tmp_path, monkeypatch)
    sent = []
    def io(self, prompt):
        sent.append(self._request_payload(prompt))
        return 'offline answer'
    monkeypatch.setattr(transport._CustomerAgentHttpRunner, 'complete', io)
    monkeypatch.setattr(walker, 'judge_client_from_env', lambda **kw: kw['controlled_permit'])
    def judge(case, reply, *, judge_client):
        judge_client.call(test_id=case.case_id, phase='judge', upper_microusd=20,
                          operation=lambda: None)
        return walker.PASSED, 'offline verdict'
    monkeypatch.setattr(walker, 'judge_starter_case', judge)
    runner = SimpleNamespace(controlled_permit=permit, controlled_target_transport=target,
                             pack_id=walker.STARTER_PACK_V4_ID, starter_cases=cases, has_tools=True,
                             complete=Mock(side_effect=AssertionError('unmetered runner')))
    try:
        rows = list(walker.customer_agent_probe_run_fn(runner))
        assert len(rows) == 100
        expected_targets = 100
        assert len(sent) == expected_targets
        assert all('prompt' in body for body in sent)
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (100, expected_targets*10+2000)
        before = len(sent)
        with pytest.raises(loader.AdmissionClosed):
            list(walker.customer_agent_probe_run_fn(runner))
        assert len(sent) == before
        runner.complete.assert_not_called()
    finally:
        permit.close()


def test_controlled_walker_stops_before_accounting_overflow(tmp_path, monkeypatch):
    from harness import controlled_permit_loader as loader
    from harness import controlled_target_transport as transport
    from tests.test_controlled_target_transport import prepared
    from tests.test_controlled_permit_loader import context, PAYLOAD
    path, record, target = prepared(tmp_path, monkeypatch, price=2**63 - 1)
    io = Mock(return_value='offline check')
    monkeypatch.setattr(transport._CustomerAgentHttpRunner, 'complete', io)
    target.preflight(record['permit_id'], tenant_id='tenant', payload=PAYLOAD, prompt='check')
    ctx = context(record)
    loader.bind_scanner_permit(ctx, scan_id='f'*32)
    permit = loader.load_bound_scanner_permit(ctx, scan_id='f'*32)
    cases = bind_cases(permit, tmp_path, monkeypatch)
    monkeypatch.setattr(walker, 'judge_client_from_env', lambda **kw: object())
    runner = SimpleNamespace(controlled_permit=permit, controlled_target_transport=target,
                             pack_id=walker.STARTER_PACK_V4_ID, starter_cases=cases, has_tools=True)
    try:
        with pytest.raises(loader.AdmissionClosed):
            list(walker.customer_agent_probe_run_fn(runner))
        assert io.call_count == 1
    finally:
        permit.close()
