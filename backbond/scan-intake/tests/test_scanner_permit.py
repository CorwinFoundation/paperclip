import hashlib
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from controlled_admission import AdmissionClosed, ControlledAdmission, OperatorAdmission, ScannerPermit
from scanners.starter_forms import reserve_starter_cases

CASE_IDS = []


@pytest.fixture(autouse=True)
def accepted_form(tmp_path, monkeypatch):
    # Every test uses a private persisted @4 form, including reopened adapters.
    monkeypatch.setenv('BACKBOND_VARIABLE_FORMS_DB', str(tmp_path / 'forms.sqlite'))
    cases, _ = reserve_starter_cases(scan_id='scan', tenant_id='tenant', agent_id='agent')
    monkeypatch.setattr(sys.modules[__name__], 'CASE_IDS', [case.case_id for case in cases])


def permit(path, initialize=True):
    payload = {'agent_endpoint': {'url': 'https://fixture.invalid'}, 'credential_secret': 'fixture'}
    ledger = ControlledAdmission(path)
    binding = dict(actor='operator', request_id='request', target=OperatorAdmission.fingerprint(payload))
    if initialize:
        ledger.provision(**binding)
        ledger.enable()
        ledger.claim(**binding)
    adapter = OperatorAdmission(ledger, **binding, token_sha256=hashlib.sha256(b'fixture').hexdigest())
    if initialize:
        adapter.bind_scan('scan')
        adapter.before_dispatch('scan', payload)
    scanner = ScannerPermit(adapter, scan_id='scan', payload=payload)
    if initialize:
        scanner.bind_accepted_form(tenant_id='tenant', agent_id='agent', pack_id='starter-100@4')
    return scanner


def test_combined_budget_survives_restart(tmp_path):
    path = tmp_path / 'ledger.sqlite'
    p = permit(path)
    calls = []
    p.call(test_id=CASE_IDS[0], phase='target', upper_microusd=6_000_000, operation=lambda: calls.append('target'))
    p = permit(path, False)
    with pytest.raises(AdmissionClosed):
        p.call(test_id=CASE_IDS[0], phase='judge', upper_microusd=4_000_001, operation=lambda: pytest.fail('over cap'))
    p.call(test_id=CASE_IDS[0], phase='judge', upper_microusd=4_000_000, operation=lambda: calls.append('judge'))
    assert calls == ['target', 'judge']
    assert p.admission.ledger.db.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (1, 10_000_000)


def test_unknown_attempt_never_repeated(tmp_path):
    path = tmp_path / 'ledger.sqlite'
    p = permit(path)
    def unknown():
        raise TimeoutError('fixture')
    with pytest.raises(TimeoutError):
        p.call(test_id=CASE_IDS[0], phase='target', upper_microusd=1, operation=unknown)
    with pytest.raises(AdmissionClosed):
        permit(path, False).call(test_id=CASE_IDS[0], phase='target', upper_microusd=1, operation=lambda: pytest.fail('replayed'))


def test_test_cap_and_unknown_cost(tmp_path):
    p = permit(tmp_path / 'ledger.sqlite')
    for i in range(100):
        p.call(test_id=CASE_IDS[i], phase='target', upper_microusd=1, operation=lambda: None)
    # Make a canonical case unseen while retaining the saturated counter.
    # This isolates the numeric cap from the separate replay/roster guards.
    p.admission.ledger.db.execute('DELETE FROM controlled_tests WHERE test_id=?', (CASE_IDS[0],))
    with pytest.raises(AdmissionClosed):
        p.call(test_id=CASE_IDS[0], phase='target', upper_microusd=1, operation=lambda: pytest.fail('over cap'))
    with pytest.raises(AdmissionClosed):
        p.call(test_id=CASE_IDS[0], phase='judge', upper_microusd=None, operation=lambda: pytest.fail('unknown price'))


def test_wrong_binding(tmp_path):
    p = permit(tmp_path / 'ledger.sqlite')
    with pytest.raises(AdmissionClosed):
        ScannerPermit(p.admission, scan_id='foreign', payload={})
    with pytest.raises(AdmissionClosed):
        ScannerPermit(p.admission, scan_id='scan', payload={'agent_endpoint': {}, 'credential_secret': 'changed'})


def test_concurrent_target_and_judge_share_one_budget(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    path = tmp_path / 'ledger.sqlite'
    permit(path)
    ready = Barrier(2)
    calls = []

    def attempt(phase):
        # Separate adapters/connections model independently running workers.
        p = permit(path, False)
        ready.wait(timeout=5)
        try:
            p.call(test_id=CASE_IDS[0], phase=phase, upper_microusd=6_000_000,
                   operation=lambda: calls.append(phase))
            return 'called'
        except AdmissionClosed:
            return 'closed'
        finally:
            p.admission.ledger.db.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(attempt, ['target', 'judge']))
    assert sorted(results) == ['called', 'closed']
    assert len(calls) == 1
    p = permit(path, False)
    assert p.admission.ledger.db.execute(
        'SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (1, 6_000_000)


def test_judge_combined_reservation_precedes_io_and_replay_is_closed(tmp_path, monkeypatch):
    from scanners.starter_judge import OpenRouterJudgeClient
    p = permit(tmp_path / 'ledger.sqlite')
    observed = []
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'choices': [{'message': {'content': 'fixture'}}]}
    def post(*args, **kwargs):
        observed.append(p.admission.ledger.db.execute(
            'SELECT reserved_microusd FROM controlled_admission').fetchone()[0])
        assert kwargs['json']['max_tokens'] == 512
        assert kwargs['json']['provider']['allow_fallbacks'] is False
        return Response()
    monkeypatch.setattr('httpx.post', post)
    client = OpenRouterJudgeClient('fixture', controlled_permit=p)
    assert client.complete(system='s', user='u', test_id=CASE_IDS[0]) == 'fixture'
    assert observed == [2 * (1026 + 512 * 4)]
    with pytest.raises(AdmissionClosed):
        client.complete(system='s', user='u', test_id=CASE_IDS[0])
    assert len(observed) == 1


@pytest.mark.parametrize('failure', ['exhausted', 'missing_identity', 'unknown_model', 'overflow'])
def test_judge_denied_before_io(tmp_path, monkeypatch, failure):
    import scanners.starter_judge as judge
    p = permit(tmp_path / 'ledger.sqlite')
    if failure == 'exhausted':
        p.call(test_id=CASE_IDS[0], phase='target', upper_microusd=10_000_000, operation=lambda: None)
    if failure == 'unknown_model':
        monkeypatch.setattr(judge, 'JUDGE_MODEL', 'unknown')
    monkeypatch.setattr('httpx.post', lambda *a, **kw: pytest.fail('denied paid I/O'))
    with pytest.raises((AdmissionClosed, judge.EvaluatorBudgetError)):
        judge.OpenRouterJudgeClient('fixture', controlled_permit=p).complete(
            system='s', user='x' * 32769 if failure == 'overflow' else 'u',
            test_id=None if failure == 'missing_identity' else CASE_IDS[0])


def test_judge_timeout_consumes_identity_and_budget(tmp_path, monkeypatch):
    from scanners.starter_judge import OpenRouterJudgeClient
    path = tmp_path / 'ledger.sqlite'
    p = permit(path)
    def post(*a, **kw): raise TimeoutError('fixture')
    monkeypatch.setattr('httpx.post', post)
    with pytest.raises(TimeoutError):
        OpenRouterJudgeClient('fixture', controlled_permit=p).complete(system='s', user='u', test_id=CASE_IDS[0])
    monkeypatch.setattr('httpx.post', lambda *a, **kw: pytest.fail('unknown replay'))
    with pytest.raises(AdmissionClosed):
        OpenRouterJudgeClient('fixture', controlled_permit=permit(path, False)).complete(
            system='s', user='u', test_id=CASE_IDS[0])


def test_noncanonical_identity_stops_before_io(tmp_path):
    p = permit(tmp_path / 'ledger.sqlite')
    with pytest.raises(AdmissionClosed, match='Selected accepted Starter'):
        p.call(test_id='one', phase='target', upper_microusd=1,
               operation=lambda: pytest.fail('noncanonical paid I/O'))
    assert p.admission.ledger.db.execute(
        'SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (0, 0)
