"""Explicit evaluator budgets and controlled shared-budget integration, offline only."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from scanners.evaluator_budget import EvaluatorBudget, EvaluatorBudgetError


def budget(path, limit=10_000_000):
    return EvaluatorBudget(path, 'synthetic-run', limit_micro=limit)


def test_restart_retains_unknown_outcome_reservations(tmp_path):
    path = tmp_path / 'budget.sqlite'
    budget(path).reserve(6_000_000)
    with pytest.raises(EvaluatorBudgetError):
        budget(path).reserve(5_000_000)
    budget(path).reserve(4_000_000)
    with pytest.raises(EvaluatorBudgetError):
        budget(path).reserve(1)


def test_concurrent_retry_reservations_cannot_exceed_cap(tmp_path):
    path = tmp_path / 'budget.sqlite'
    budget(path)
    def attempt(_):
        try:
            budget(path).reserve(1_000_000)
            return 1
        except EvaluatorBudgetError:
            return 0
    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(attempt, range(40))) == 10
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT reserved_micro FROM evaluator_budget').fetchone() == (10_000_000,)


@pytest.mark.parametrize('amount', [0, -1, None, True, 1.5, float('inf'), 10_000_001])
def test_unknown_invalid_overflow_cost_fails_closed(tmp_path, amount):
    with pytest.raises(EvaluatorBudgetError):
        budget(tmp_path / 'budget.sqlite').reserve(amount)


def test_limit_cannot_change_after_restart(tmp_path):
    path = tmp_path / 'budget.sqlite'
    budget(path)
    with pytest.raises(EvaluatorBudgetError):
        budget(path, 9_000_000)


@pytest.mark.parametrize('limit', [None, 0, -1, 10_000_001, True, float('inf'), float('nan'), 0.1])
def test_explicit_budget_invalid_ceiling_fails_before_dispatch(tmp_path, monkeypatch, limit):
    monkeypatch.setattr('httpx.post', lambda *a, **k: pytest.fail('model dispatch forbidden'))
    with pytest.raises(EvaluatorBudgetError):
        budget(tmp_path / 'budget.sqlite', limit=limit)
    assert not (tmp_path / 'budget.sqlite').exists()


def test_no_ephemeral_fallback(tmp_path):
    with pytest.raises(EvaluatorBudgetError):
        budget(':memory:')
    with pytest.raises(sqlite3.OperationalError):
        budget(tmp_path / 'missing' / 'budget.sqlite')


def test_paid_boundary_reserves_before_http_and_retains_timeout(tmp_path, monkeypatch):
    import httpx
    from scanners.starter_judge import OpenRouterJudgeClient
    path = tmp_path / 'budget.sqlite'
    calls = []
    def post(*args, **kwargs):
        with sqlite3.connect(path) as db:
            reserved = db.execute('SELECT reserved_micro FROM evaluator_budget').fetchone()[0]
        body = kwargs['json']
        assert reserved >= 2 * (1026 + 512 * 4)
        assert body['model'] == 'openai/gpt-4.1-mini'
        assert body['max_tokens'] == 512
        assert body['provider'] == {'allow_fallbacks': False, 'require_parameters': True,
                                    'max_price': {'prompt': 1, 'completion': 4, 'request': 0}}
        calls.append(reserved)
        raise httpx.ReadTimeout('synthetic unknown result')
    monkeypatch.setattr(httpx, 'post', post)
    for _ in range(2):
        client = OpenRouterJudgeClient('synthetic', budget=budget(path))
        with pytest.raises(httpx.ReadTimeout):
            client.complete(system='s', user='u')
    assert len(calls) == 2 and calls[1] == 2 * calls[0]


def test_budget_exhaustion_overflow_and_unknown_model_never_reach_http(tmp_path, monkeypatch):
    import httpx
    import scanners.starter_judge as judge
    monkeypatch.setattr(httpx, 'post', lambda *a, **k: pytest.fail('HTTP must be blocked'))
    path = tmp_path / 'budget.sqlite'
    ledger = budget(path)
    client = judge.OpenRouterJudgeClient('synthetic', budget=ledger)
    with pytest.raises(EvaluatorBudgetError):
        client.complete(system='s', user='x' * 32768)
    monkeypatch.setattr(judge, 'JUDGE_MODEL', 'unknown-model')
    with pytest.raises(EvaluatorBudgetError):
        client.complete(system='s', user='u')
    monkeypatch.setattr(judge, 'JUDGE_MODEL', 'openai/gpt-4.1-mini')
    ledger.reserve(10_000_000)
    with pytest.raises(EvaluatorBudgetError):
        client.complete(system='s', user='u')


def test_success_is_charged_and_judge_answer_preserved(tmp_path, monkeypatch):
    import httpx
    from scanners.starter_judge import OpenRouterJudgeClient
    class Reply:
        def raise_for_status(self): pass
        def json(self): return {'choices': [{'message': {'content': 'synthetic answer'}}]}
    monkeypatch.setattr(httpx, 'post', lambda *a, **k: Reply())
    path = tmp_path / 'budget.sqlite'
    assert OpenRouterJudgeClient('synthetic', budget=budget(path)).complete(system='s', user='u') == 'synthetic answer'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT reserved_micro FROM evaluator_budget').fetchone()[0] > 0


@pytest.fixture
def real_glue(tmp_path, monkeypatch):
    """Actual controlled orchestrator, saved @4 form, target and judge guards.

    Ordinary @4 has no private rehearsal budget dependency on master. Controlled
    execution uses the authenticated shared permit, never that retired env var.
    Only target/model I/O is replaced; reservation and policy loading stay real.
    """
    import io
    import tarfile
    from types import SimpleNamespace
    from harness.credentials import CredentialVault
    from harness import controlled_permit_loader as loader
    from harness import controlled_target_transport as transport
    from scanners.live_probe_orchestrator import ScanRequest, run_scan
    from scanners.customer_agent_walker import customer_agent_probe_run_fn
    from scanners.snapshot_hash import compute_corpus_snapshot_hash
    from scanners.starter_forms import reserve_starter_cases
    from tests.test_controlled_permit_loader import PAYLOAD, setup_state, arm_fixture, context
    from tests.test_controlled_target_policy import policy_file

    corpus = tmp_path / 'corpus.bin'
    with tarfile.open(corpus, 'w:gz') as archive:
        payload = b'synthetic pinned corpus'
        member = tarfile.TarInfo('manifest.json')
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    monkeypatch.setenv('BACKBOND_VARIABLE_FORMS_DB', str(tmp_path / 'forms.sqlite'))
    monkeypatch.setenv('OPENROUTER_API_KEY', 'synthetic-not-a-key')
    monkeypatch.delenv('SCANNER_EVALUATOR_BUDGET_DB', raising=False)
    scan_id = 'a' * 32
    path, record = setup_state(tmp_path, monkeypatch)
    arm_fixture(path)
    ctx = context(record)
    cases, _ = reserve_starter_cases(scan_id=scan_id, tenant_id='tenant', agent_id='agent')
    assert len(cases) == len({case.case_id for case in cases}) == 100
    policy_path, digest = policy_file(tmp_path, upper_microusd=100)
    config = dict(path=str(policy_path), expected_sha256=digest)
    calls = []
    def target_io(self, prompt):
        calls.append('target')
        return 'I can help with ordinary questions while respecting safety constraints.'
    monkeypatch.setattr(transport._CustomerAgentHttpRunner, 'complete', target_io)
    class Runner:
        def complete(self, prompt):
            pytest.fail('unmetered target dispatch forbidden')
    class IPC:
        def send_row(self, *args): pass
        def send_end_of_scan(self): pass
    bound = False
    def execute():
        nonlocal bound
        if not bound:
            loader.bind_scanner_permit(ctx, scan_id=scan_id)
            bound = True
        vault = CredentialVault()
        vault.register('synthetic-ref', PAYLOAD['credential_secret'])
        request = ScanRequest(
            scan_id=scan_id, tenant_id='tenant', agent_id='agent',
            agent_endpoint=PAYLOAD['agent_endpoint']['url'], credential_ref='synthetic-ref',
            endpoint_config={'_controlled_permit': ctx},
            corpus_snapshot_hash=compute_corpus_snapshot_hash(corpus),
            scanner_version='test', corpus_version='test', seed=42,
            pack_id='starter-100@4', max_cost_usd=10, k_replicates=5)
        return run_scan(request, vault, IPC(), corpus_tarball_path=corpus,
                        probe_run_fn=customer_agent_probe_run_fn, runner=Runner(),
                        controlled_target_config=config, controlled_execution_payload=PAYLOAD)
    return SimpleNamespace(execute=execute, calls=calls, path=path, config=config,
                           policy_path=policy_path, cases=cases, scan_id=scan_id)


@pytest.mark.parametrize('cost', [None, -1, 10_000_001, True, float('inf'), float('nan'), 0.1, 1.5])
def test_controlled_invalid_budget_fails_before_target(cost, real_glue, tmp_path, monkeypatch):
    from harness.controlled_permit_loader import AdmissionClosed
    from tests.test_controlled_target_policy import policy_file
    monkeypatch.setattr('httpx.post', lambda *a, **k: pytest.fail('model dispatch forbidden'))
    path, digest = policy_file(tmp_path, upper_microusd=cost)
    real_glue.config.update(path=str(path), expected_sha256=digest)
    with pytest.raises(AdmissionClosed):
        real_glue.execute()
    assert real_glue.calls == []
    with sqlite3.connect(real_glue.path) as db:
        assert db.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (0, 0)


def test_real_glue_scan_identity_ceiling_and_restart_ledger(real_glue, monkeypatch):
    from harness.controlled_permit_loader import AdmissionClosed
    reservations = []
    class BoundaryReached(BaseException): pass
    def post(*args, **kwargs):
        with sqlite3.connect(real_glue.path) as db:
            assert db.execute('SELECT scan_id FROM controlled_scan_binding').fetchone() == (real_glue.scan_id,)
            tests, reserved = db.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone()
            assert tests == 1 and 100 < reserved <= 10_000_000
            assert db.execute("SELECT state FROM controlled_paid_calls WHERE call_id LIKE '%judge%'").fetchone() == ('call_unknown',)
        reservations.append(reserved)
        raise BoundaryReached()
    monkeypatch.setattr('httpx.post', post)
    with pytest.raises(BoundaryReached):
        real_glue.execute()
    assert real_glue.calls == ['target']
    # New orchestrator/permit connection reuses the saved form and consumed call
    # identity. Unknown acceptance is never dispatched again or refunded.
    with pytest.raises(AdmissionClosed):
        real_glue.execute()
    assert real_glue.calls == ['target']
    with sqlite3.connect(real_glue.path) as db:
        assert db.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (1, reservations[0])
    assert len(reservations) == 1


@pytest.mark.parametrize('path_kind', ['absent', 'relative', 'memory', 'missing_directory'])
def test_real_glue_invalid_config_blocks_all_dispatch(real_glue, tmp_path, monkeypatch, path_kind):
    from harness.controlled_permit_loader import AdmissionClosed
    monkeypatch.setattr('httpx.post', lambda *a, **k: pytest.fail('model dispatch forbidden'))
    monkeypatch.delenv('ARE_CONTROLLED_PERMIT_DB', raising=False)
    paths = {'relative': 'permit.sqlite', 'memory': ':memory:',
             'missing_directory': str(tmp_path / 'missing' / 'permit.sqlite')}
    if path_kind in paths:
        monkeypatch.setenv('ARE_CONTROLLED_PERMIT_DB', paths[path_kind])
    with pytest.raises(AdmissionClosed):
        real_glue.execute()
    assert real_glue.calls == []
    with sqlite3.connect(real_glue.path) as db:
        assert db.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (0, 0)


def test_real_glue_exhausted_shared_budget_blocks_all_dispatch(real_glue, monkeypatch):
    from harness.controlled_permit_loader import AdmissionClosed
    # The sole natural check can spend the entire cap before Scanner starts.
    # Reserve through the real preflight guard, without target/model I/O.
    from harness import controlled_permit_loader as loader
    admission = loader._load('p' * 32, 'tenant')
    try:
        admission.ledger.preflight_call(**admission.binding, upper_microusd=10_000_000, operation=lambda: None)
    finally:
        admission.ledger.db.close()
    monkeypatch.setattr('httpx.post', lambda *a, **k: pytest.fail('model dispatch forbidden'))
    with pytest.raises(AdmissionClosed):
        real_glue.execute()
    assert real_glue.calls == []
    with sqlite3.connect(real_glue.path) as db:
        # Case identity is consumed before spend reservation; exhaustion never
        # permits a target call or refunds/rearms that attempt.
        assert db.execute('SELECT tests,reserved_microusd FROM controlled_admission').fetchone() == (1, 10_000_000)
