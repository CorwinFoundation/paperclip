"""Completed target work survives a report-side exception and process restart."""
import threading
from dataclasses import replace

from api.accepted_scan_store import AcceptedScanStore
from api.scan_handlers import ScanRuntime
from scanners.live_probe_orchestrator import ScanRequest, ScanResult


def test_report_failure_retains_execution_across_restart(tmp_path):
    path = tmp_path / 'accepted.sqlite3'
    store = AcceptedScanStore(path)
    request = ScanRequest(
        scan_id='scan-1', tenant_id='owner', agent_endpoint='https://agent.invalid',
        credential_ref='secret-ref', corpus_snapshot_hash='sha256:original',
        scanner_version='original-scanner', corpus_version='original-corpus', seed=0,
        pack_id='starter-100@4', agent_id='agent-1',
    )
    result = ScanResult(
        scan_id=request.scan_id, status='incomplete', rows_emitted=100,
        scanner_version=request.scanner_version, corpus_version=request.corpus_version,
        starter_result_rows=({'case_id': 'retained-case'},),
        evidence_records=({'response': 'private retained evidence'},),
    )
    store.accept(request, agent_id='agent-1', invocation_context=None, webhook_cfg=None)
    for index in range(100):
        store.record_case_progress('scan-1', case_id=f'case-{index}', family='safety', state='finished')
    runtime = ScanRuntime(run_scan_fn=lambda *_: None, accepted_scan_store=store)
    runtime._full_scan_semaphore = threading.Semaphore(1)
    target_calls = []
    def execute(*args, **kwargs):
        target_calls.append(True)
        return result
    runtime._apply_registry_dispatch_guard = execute
    def report(*args, **kwargs):
        # The durable checkpoint must exist before the failing report call.
        assert store.execution_checkpoint('scan-1', tenant_id='owner') == result
        raise ValueError('renderer unavailable')
    runtime._record_completed_result = report
    runtime._run_full_scan_background(scan_request=request, secret='credential', agent_id='agent-1')
    assert len(target_calls) == 1
    reopened = AcceptedScanStore(path)
    retained = reopened.get('scan-1')['result']
    assert retained.rows_emitted == 100
    progress = ScanRuntime(run_scan_fn=lambda *_: None, accepted_scan_store=reopened).progress('scan-1', tenant_id='owner')
    assert progress['processed_count'] == progress['usable_count'] == 100
    assert progress['accepted_pack_total'] == 100
    assert progress['unavailable_count'] == 0
    assert progress['failed_stage'] == 'report_preparation'
    assert progress['execution_completed'] is True
    assert progress['report_retry_allowed'] is True
    assert progress['retry_allowed'] is False
    assert progress['state'] == 'incomplete'
    assert retained.starter_result_rows == result.starter_result_rows
    assert retained.incomplete_reason == 'report_preparation_error:ValueError'
    checkpoint = reopened.execution_checkpoint('scan-1', tenant_id='owner')
    assert checkpoint == result
    assert reopened.execution_checkpoint('scan-1', tenant_id='foreign') is None
    reopened.checkpoint_execution('scan-1', replace(result, rows_emitted=0))
    assert reopened.execution_checkpoint('scan-1', tenant_id='owner') == result


def test_report_retry_and_restart_never_execute_target(tmp_path):
    import pytest
    from api.scan_handlers import HandlerError

    path = tmp_path / 'retry.sqlite3'
    store = AcceptedScanStore(path)
    request = ScanRequest(
        scan_id='retry-1', tenant_id='owner', agent_endpoint='https://agent.invalid',
        credential_ref='forgotten-secret', corpus_snapshot_hash='sha256:original',
        scanner_version='original-scanner', corpus_version='original-corpus', seed=0,
    )
    original = ScanResult(
        scan_id=request.scan_id, status='completed', rows_emitted=100,
        scanner_version=request.scanner_version, corpus_version=request.corpus_version,
        evidence_records=({'response': 'retained private evidence'},),
    )
    store.accept(request, agent_id=None, invocation_context=None, webhook_cfg=None)
    store.checkpoint_execution(request.scan_id, original)
    runtime = ScanRuntime(run_scan_fn=lambda *_: None, accepted_scan_store=AcceptedScanStore(path))
    def no_target(*args, **kwargs):
        pytest.fail('report recovery attempted target traffic')
    runtime._start_background_scan = no_target
    runtime._run_scan_fn = no_target
    runtime._apply_registry_dispatch_guard = no_target
    reports = []
    finalize = runtime._record_completed_result
    def report(req, result, **kwargs):
        assert result == original
        assert req == request
        reports.append(result)
        if len(reports) <= 2:
            raise ValueError('renderer down')
        return finalize(req, result, **kwargs)
    runtime._record_completed_result = report
    # Startup must recover before testing whether a credential can be replayed.
    assert runtime.resume_accepted_scans(start_webhook_retry=False)['resumed'] == ['retry-1']
    with pytest.raises(HandlerError):
        runtime.retry_report('retry-1', tenant_id='foreign')
    failed = runtime.retry_report('retry-1', tenant_id='owner')
    assert failed.rows_emitted == 100
    assert failed.evidence_records == original.evidence_records
    assert failed.incomplete_reason == 'report_preparation_error:ValueError'
    assert runtime.retry_report('retry-1', tenant_id='owner') == original
    persisted = runtime.retry_report('retry-1', tenant_id='owner')
    assert persisted == replace(original, evidence_records=())
    assert runtime._accepted_scan_store.execution_checkpoint('retry-1', tenant_id='owner') == original
    assert len(reports) == 3


def test_authenticated_report_retry_route(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from api.app import create_app
    from api.bundle import BundleStore

    monkeypatch.setenv("ARE_API_AUTH_TOKENS_JSON", '{"owner-token":"owner","foreign-token":"foreign"}')
    store = AcceptedScanStore(tmp_path / "http-retry.sqlite3")
    request = ScanRequest(
        scan_id="http-retry", tenant_id="owner", agent_endpoint="https://agent.invalid",
        credential_ref="gone", corpus_snapshot_hash="sha256:original",
        scanner_version="original", corpus_version="original", seed=0,
    )
    original = ScanResult(scan_id=request.scan_id, status="completed", rows_emitted=100,
                          scanner_version="original", corpus_version="original")
    runtime = ScanRuntime(run_scan_fn=lambda *_: (_ for _ in ()).throw(AssertionError("target called")),
                          accepted_scan_store=store)
    client = TestClient(create_app(bundles=BundleStore(), scan_runtime=runtime))
    store.accept(request, agent_id=None, invocation_context=None, webhook_cfg=None)
    store.checkpoint_execution(request.scan_id, original)
    url = "/scanner/v1/scans/http-retry/retry-report"
    assert client.post(url).status_code == 401
    assert client.post(url, headers={"Authorization": "Bearer foreign-token"}).status_code == 404
    finalize = runtime._record_completed_result
    def fail(*args, **kwargs):
        raise ValueError("private renderer detail")
    runtime._record_completed_result = fail
    response = client.post(url, headers={"Authorization": "Bearer owner-token"})
    assert response.json() == {"scan_id": "http-retry", "report_status": "failed", "report_retry_allowed": True}
    assert store.get(request.scan_id)["result"].rows_emitted == 100
    runtime._record_completed_result = finalize
    for _ in range(2):
        response = client.post(url, headers={"Authorization": "Bearer owner-token"})
        assert response.status_code == 200
        assert response.json() == {"scan_id": "http-retry", "report_status": "prepared", "report_retry_allowed": False}
    assert client.post("/scanner/v1/scans/missing/retry-report", headers={"Authorization": "Bearer owner-token"}).status_code == 404
