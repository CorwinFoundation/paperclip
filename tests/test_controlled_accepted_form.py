"""Offline real @4 assembly -> persistent permit -> injected paid boundary."""
from collections import Counter
import hashlib
import json
import sqlite3

import pytest

from harness import controlled_permit_loader as loader
from scanners.starter_forms import reserve_starter_cases, starter_form_store
from scanners.starter_pack_v4 import starter_v4_scenarios
from tests.test_controlled_permit_loader import setup_state, arm_fixture, context, PAYLOAD

SCAN = '0123456789abcdef0123456789abcdef'
SCOPE = dict(tenant_id='tenant', agent_id='agent', pack_id='starter-100@4')


def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv('BACKBOND_VARIABLE_FORMS_DB', str(tmp_path / 'forms.sqlite'))
    cases, summary = reserve_starter_cases(scan_id=SCAN, tenant_id='tenant', agent_id='agent')
    path, record = setup_state(tmp_path, monkeypatch)
    arm_fixture(path)
    ctx = context(record)
    loader.execute_preflight(record['permit_id'], tenant_id='tenant',
        payload=PAYLOAD,
        upper_microusd=100, operation=lambda: None)
    loader.bind_scanner_permit(ctx, scan_id=SCAN)
    permit = loader.load_bound_scanner_permit(ctx, scan_id=SCAN)
    permit.bind_accepted_form(**SCOPE)
    return cases, summary, path, ctx, permit


def totals(permit):
    return permit.admission.ledger.db.execute(
        'SELECT tests,reserved_microusd FROM controlled_admission').fetchone()


def test_all_100_cases_account_above_old_cap_and_survive_restart(tmp_path, monkeypatch):
    cases, summary, path, ctx, permit = fixture(tmp_path, monkeypatch)
    selected = {c.case_id for c in cases}
    attempted = set()
    assert len(selected) == 100
    assert set(Counter(c.family_id for c in cases).values()) == {10}
    assert summary['bank_version'] == 'starter-bank@4.1'
    for case in cases:
        permit.call(test_id=case.case_id, phase='target', upper_microusd=99_999,
                    operation=lambda case=case: attempted.add(case.case_id))
    assert attempted == selected
    ledger_ids = {json.loads(row[0])[1] for row in permit.admission.ledger.db.execute(
        "SELECT call_id FROM controlled_paid_calls")}
    assert ledger_ids == selected
    permit.call(test_id=cases[0].case_id, phase='evaluator', upper_microusd=12_000_000, operation=lambda: None)
    assert totals(permit) == (100, 22_000_000)
    unselected = next(s['payload']['case_id'] for s in starter_v4_scenarios()
                      if s['payload']['case_id'] not in selected)
    for test_id, phase in [(unselected, 'target'), ('arbitrary', 'target'),
                           (cases[0].case_id, 'target')]:
        with pytest.raises(loader.AdmissionClosed):
            permit.call(test_id=test_id, phase=phase, upper_microusd=1,
                        operation=lambda: pytest.fail('extra I/O'))
        assert totals(permit) == (100, 22_000_000)
    permit.close()
    replay, replay_summary = reserve_starter_cases(scan_id=SCAN, tenant_id='tenant', agent_id='agent')
    assert replay == cases and replay_summary == summary
    permit = loader.load_bound_scanner_permit(ctx, scan_id=SCAN)
    permit.bind_accepted_form(**SCOPE)
    with pytest.raises(loader.AdmissionClosed):
        permit.call(test_id=cases[0].case_id, phase='target', upper_microusd=1,
                    operation=lambda: pytest.fail('restart retry'))
    assert totals(permit) == (100, 22_000_000)
    permit.close()


def test_unknown_replay_nonselected_and_replaced_form_stop_without_spend(tmp_path, monkeypatch):
    cases, _, path, ctx, permit = fixture(tmp_path, monkeypatch)
    selected = {c.case_id for c in cases}
    unselected = next(s['payload']['case_id'] for s in starter_v4_scenarios()
                      if s['payload']['case_id'] not in selected)
    for test_id in (unselected, 'arbitrary'):
        with pytest.raises(loader.AdmissionClosed):
            permit.call(test_id=test_id, phase='target', upper_microusd=1,
                        operation=lambda: pytest.fail('unselected I/O'))
    assert totals(permit) == (0, 100)
    def unknown():
        raise TimeoutError('lost response')
    with pytest.raises(TimeoutError):
        permit.call(test_id=cases[0].case_id, phase='target', upper_microusd=7, operation=unknown)
    permit.close()
    permit = loader.load_bound_scanner_permit(ctx, scan_id=SCAN)
    with pytest.raises(loader.AdmissionClosed):
        permit.call(test_id=cases[0].case_id, phase='target', upper_microusd=7,
                    operation=lambda: pytest.fail('unknown retry'))
    assert totals(permit) == (1, 107)
    for scope in (dict(SCOPE, tenant_id='foreign'), dict(SCOPE, agent_id='foreign'),
                  dict(SCOPE, pack_id='starter-100@5')):
        with pytest.raises(loader.AdmissionClosed):
            permit.bind_accepted_form(**scope)
    # Replace with a hash-valid manifest to verify our persisted binding, not
    # merely FormStore's own integrity check. No production form is touched.
    store = starter_form_store()
    reserve_starter_cases(scan_id='replacement', tenant_id='tenant', agent_id='agent')
    manifest = store.get('replacement', scope=('tenant', 'agent'), product='starter')
    manifest['form_id'] = SCAN
    with sqlite3.connect(store.path) as db:
        encoded = json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        db.execute('UPDATE variable_test_forms SET manifest=?,content_hash=? WHERE form_id=?',
                   (encoded, hashlib.sha256(encoded.encode()).hexdigest(), SCAN))
    with pytest.raises(loader.AdmissionClosed):
        permit.bind_accepted_form(**SCOPE)
    with pytest.raises(loader.AdmissionClosed):
        permit.call(test_id=cases[1].case_id, phase='target', upper_microusd=1,
                    operation=lambda: pytest.fail('changed form I/O'))
    assert totals(permit) == (1, 107)
    permit.close()


@pytest.mark.parametrize('price', [None, True, 0, -1, 2**63])
def test_selected_case_unknown_or_excess_price_stops_before_io(tmp_path, monkeypatch, price):
    cases, _, _, _, permit = fixture(tmp_path, monkeypatch)
    try:
        with pytest.raises(loader.AdmissionClosed):
            permit.call(test_id=cases[0].case_id, phase='target', upper_microusd=price,
                        operation=lambda: pytest.fail('unpriced I/O'))
        assert totals(permit)[1] == 100
        assert permit.admission.ledger.db.execute(
            'SELECT COUNT(*) FROM controlled_paid_calls').fetchone() == (0,)
    finally:
        permit.close()
