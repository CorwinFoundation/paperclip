from dataclasses import replace

import pytest

from tools.registered_case_executor import (
    BudgetExceeded, ScanBudget, ScanBudgetLedger, parse_scan_cost_limit,
)


def test_unlimited_keeps_charges_and_call_limits():
    ledger = ScanBudgetLedger(ScanBudget(max_judge_calls=1, max_target_calls=1))
    reserved = ledger.reserve_judge_call('prompt')
    ledger.reconcile_judge_cost(reserved, 2000.0)
    assert ledger.estimated_judge_cost_usd == 2000.0
    assert ledger.case_snapshot()['estimated_judge_cost_usd'] == 2000.0
    with pytest.raises(BudgetExceeded, match='max_judge_calls'):
        ledger.reserve_judge_call('prompt')
    ledger.reserve_target_call()
    with pytest.raises(BudgetExceeded, match='max_target_calls'):
        ledger.reserve_target_call()


def test_unlimited_keeps_deadline():
    ledger = ScanBudgetLedger(ScanBudget(max_wall_clock_seconds=-1))
    with pytest.raises(BudgetExceeded, match='max_wall_clock_seconds'):
        ledger.reserve_target_call()


@pytest.mark.parametrize('value', ['nan', 'inf', '-1', ''])
def test_bad_limits_rejected(value):
    with pytest.raises(ValueError):
        parse_scan_cost_limit(value)


def test_zero_remains_zero_and_finite_opt_in_still_works(monkeypatch):
    assert parse_scan_cost_limit('0') == 0
    ledger = ScanBudgetLedger(ScanBudget(max_estimated_judge_cost_usd=0))
    with pytest.raises(BudgetExceeded, match='max_estimated_judge_cost_usd'):
        ledger.reserve_judge_call('prompt')
    monkeypatch.setenv('ARE_FULL_SCAN_MAX_JUDGE_COST_USD', '2')
    assert ScanBudget.from_env().max_estimated_judge_cost_usd == 2
    monkeypatch.setenv('ARE_FULL_SCAN_MAX_JUDGE_COST_USD', 'unlimited')
    assert ScanBudget.from_env().max_estimated_judge_cost_usd is None


def test_scheduler_accepts_lift_without_relaxing_operational_limits(monkeypatch, tmp_path):
    from scripts.scheduled_scan import ScheduleConfig
    for key, value in {
        'TENANT_ID': 'fixture', 'AGENT_ENDPOINT': 'https://example.test',
        'EVIDENCE_ROOT': str(tmp_path), 'SCANNER_VERSION': 'fixture',
        'CORPUS_VERSION': 'fixture', 'JUDGE_MODEL': 'fixture',
        'COST_CAP_USD': 'unlimited',
    }.items():
        monkeypatch.setenv('BACKBOND_SCAN_' + key, value)
    config = ScheduleConfig.from_environment()
    config.validate()
    assert config.cost_cap_usd is None
    replace(config, cost_cap_usd=2000).validate()
    for change in [dict(concurrency=2), dict(rpm=31), dict(retries=1)]:
        with pytest.raises(RuntimeError):
            replace(config, **change).validate()


@pytest.mark.parametrize('limit,allowed', [(None, True), ('2000', True), ('100', False), ('0', False)])
def test_legacy_scheduler_retains_accounting_and_optional_limit(monkeypatch, tmp_path, limit, allowed):
    import json
    from scripts import run_scheduled_full_scan as runner
    monkeypatch.setattr('sys.argv', ['runner', '--job', 'zoo'])
    for key in ['SCAN_CONCURRENCY', 'SCAN_RPM', 'SCAN_RETRIES', 'SCAN_COST_CAP_USD']:
        monkeypatch.delenv(key, raising=False)
    for key in ['SCAN_TENANT_ID', 'ZOO_AGENT_ENDPOINT', 'ZOO_CREDENTIAL_REF',
                'ZOO_CREDENTIAL_SECRET', 'SCAN_CORPUS_SNAPSHOT_HASH',
                'SCAN_SCANNER_VERSION', 'SCAN_CORPUS_VERSION', 'SCAN_API_BASE_URL']:
        monkeypatch.setenv(key, 'fixture')
    monkeypatch.setenv('SCAN_MANIFEST_ROOT', str(tmp_path))
    if limit is not None:
        monkeypatch.setenv('SCAN_COST_CAP_USD', limit)
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        return {'status': 'complete', 'estimated_cost_usd': 1500, 'scan_id': 'fixture'}
    monkeypatch.setattr(runner, '_request_json', request)
    if allowed:
        assert runner.main() == 0
        manifest = json.loads(next(tmp_path.rglob('manifest.json')).read_text())
        assert manifest['estimated_cost_usd'] == 1500
    else:
        with pytest.raises(RuntimeError, match='exceeds cap'):
            runner.main()
    assert len(calls) == 1
    monkeypatch.setenv('SCAN_CONCURRENCY', '2')
    with pytest.raises(SystemExit, match='caps exceeded'):
        runner.main()
    assert len(calls) == 1


def test_historical_replay_lifts_dollars_and_retains_exact_call_and_time_bounds():
    from scripts.beaaa30934_replay_remaining import replay_budget
    budget = replay_budget()
    assert budget.max_wall_clock_seconds == 7200
    ledger = ScanBudgetLedger(budget)
    reserved = ledger.reserve_judge_call("fixture")
    ledger.reconcile_judge_cost(reserved, 2000.0)
    assert ledger.estimated_judge_cost_usd == 2000.0
    for _ in range(99):
        ledger.reserve_judge_call("fixture")
    with pytest.raises(BudgetExceeded, match="max_judge_calls"):
        ledger.reserve_judge_call("fixture")
    for _ in range(100):
        ledger.reserve_target_call()
    with pytest.raises(BudgetExceeded, match="max_target_calls"):
        ledger.reserve_target_call()


@pytest.mark.parametrize("override", [None, "100", "0"])
def test_image_default_reaches_scheduler_ledger(monkeypatch, override):
    import shlex
    from pathlib import Path
    from scripts.scheduled_scan import ScheduleConfig

    # Load the actual packaged ENV, so a Docker-only finite default is caught.
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    instructions = dockerfile.read_text().replace("\\\n", " ").splitlines()
    for instruction in instructions:
        if instruction.startswith("ENV "):
            for assignment in shlex.split(instruction[4:]):
                key, value = assignment.split("=", 1)
                monkeypatch.setenv(key, value)
    if override is not None:
        monkeypatch.setenv("BACKBOND_SCAN_COST_CAP_USD", override)
    config = ScheduleConfig.from_environment()
    config.validate()
    assert config.cost_cap_usd == (None if override is None else float(override))
    ledger = ScanBudgetLedger(ScanBudget(
        max_estimated_judge_cost_usd=config.cost_cap_usd,
    ))
    if override == "0":
        with pytest.raises(BudgetExceeded, match="max_estimated_judge_cost_usd"):
            ledger.reserve_judge_call("fixture")
        return
    reserved = ledger.reserve_judge_call("fixture")
    if override is not None:
        with pytest.raises(BudgetExceeded, match="max_estimated_judge_cost_usd"):
            ledger.reconcile_judge_cost(reserved, 2000.0)
    else:
        ledger.reconcile_judge_cost(reserved, 2000.0)
        assert ledger.estimated_judge_cost_usd == 2000.0
        ledger.reserve_judge_call("fixture")
