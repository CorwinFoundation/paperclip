"""The special Syllable transport must satisfy the starter report contract."""
import pytest
from harness.customer_runner import customer_agent_http_runner_factory
from harness.runner import SyllableRunner, RunnerError
from api.scan_handlers import ScanRuntime
from scanners.live_probe_orchestrator import ScanRequest, ScanResult


def test_syllable_retains_100_scored_rows_for_report(monkeypatch):
    monkeypatch.setattr(SyllableRunner, 'complete', lambda self, prompt: 'reply private-token')
    runner = customer_agent_http_runner_factory(
        'https://apichat.syllable.cloud', 'private-token',
        endpoint_config={'_include_result_rows': True},
    )
    runner.pack_id = 'starter-100@4'
    runner.starter_form_summary = {'form_id': 'saved-form'}
    for index in range(100):
        runner.complete(f'prompt {index} private-token')
        runner.record_starter_verdict(test_id=f'case-{index}', family='safety', verdict='pass', reason='judged')
    rows = runner.starter_result_rows()
    assert len(rows) == 100
    assert all(row['form_id'] == 'saved-form' for row in rows)
    assert 'private-token' not in str(rows)
    req = ScanRequest(scan_id='scan', tenant_id='tenant', agent_endpoint='https://apichat.syllable.cloud',
                      credential_ref='ref', corpus_snapshot_hash='hash', scanner_version='test',
                      corpus_version='test', seed=0, pack_id='starter-100@4', include_result_rows=True)
    result = ScanResult(scan_id='scan', status='complete', scanner_version='test', corpus_version='test',
                        rows_emitted=100, starter_result_rows=rows)
    assert ScanRuntime(run_scan_fn=lambda *_: None)._record_completed_result(req, result, agent_id=None) == result


def test_syllable_error_does_not_reuse_previous_reply(monkeypatch):
    monkeypatch.setattr(SyllableRunner, 'complete', lambda self, prompt: 'old reply')
    runner = customer_agent_http_runner_factory('https://apichat.syllable.cloud', 'secret', endpoint_config={'_include_result_rows': True})
    runner.complete('old prompt')
    def fail(self, prompt):
        raise RunnerError('unavailable')
    monkeypatch.setattr(SyllableRunner, 'complete', fail)
    with pytest.raises(RunnerError):
        runner.complete('new prompt')
    runner.record_starter_verdict(test_id='failed', family='safety', verdict='could_not_run', reason='unavailable')
    assert 'reply' not in runner.starter_result_rows()[0]
