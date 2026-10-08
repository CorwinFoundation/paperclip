import pytest
from fastapi.testclient import TestClient

from api.accepted_scan_store import AcceptedScanStore
from api.app import create_app
from api.bundle import BundleStore
from api.scan_handlers import ScanRuntime
from scanners.live_probe_orchestrator import ScanRequest, ScanResult


def test_case_progress_is_durable_monotonic_and_owner_scoped(tmp_path):
    path = tmp_path / "accepted.sqlite3"
    now = [100.0]
    store = AcceptedScanStore(path, clock=lambda: now[0])
    request = ScanRequest(
        scan_id="scan-1", tenant_id="owner", agent_endpoint="https://agent.invalid",
        credential_ref="secret-ref", corpus_snapshot_hash="sha256:test",
        scanner_version="test", corpus_version="test", seed=0,
        pack_id="starter-100@4", agent_id="agent-1",
    )
    store.accept(
        request, agent_id="agent-1", invocation_context=None, webhook_cfg=None
    )
    store.mark_running("scan-1")
    store.record_case_progress("scan-1", case_id="case-1", family="safety", state="executing", occurred_at=110)
    store.record_case_progress("scan-1", case_id="case-1", family="safety", state="finished", occurred_at=120)
    store.record_case_progress("scan-1", case_id="case-1", family="safety", state="executing", occurred_at=115)
    now[0] = 130
    progress = AcceptedScanStore(path, clock=lambda: now[0]).case_progress("scan-1", tenant_id="owner")
    assert progress["state"] == "running"
    assert progress["processed_count"] == progress["usable_count"] == 1
    assert progress["accepted_pack_total"] == 100
    assert progress["timing"] == {
        "elapsed_seconds": 30.0, "queue_seconds": 10.0, "execution_seconds": 20.0,
        "observed_rate_per_second": 0.05,
        "estimated_remaining_seconds": {"low": 1584.0, "high": 2376.0},
    }
    assert "secret" not in repr(progress)
    with pytest.raises(KeyError):
        AcceptedScanStore(path, clock=lambda: now[0]).case_progress("scan-1", tenant_id="other")


def test_http_route_projects_accepted_scan_and_hides_foreign_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("ARE_API_AUTH_TOKENS_JSON", '{"owner-token":"owner","foreign-token":"other"}')
    store = AcceptedScanStore(tmp_path / "accepted.sqlite3")
    request = ScanRequest(
        scan_id="scan-1", tenant_id="owner", agent_endpoint="https://agent.invalid",
        credential_ref="secret-ref", corpus_snapshot_hash="sha256:test",
        scanner_version="test", corpus_version="test", seed=0,
        pack_id="starter-100@4", agent_id="agent-1",
    )
    runtime = ScanRuntime(run_scan_fn=lambda *_: None, accepted_scan_store=store)
    client = TestClient(create_app(bundles=BundleStore(), scan_runtime=runtime))
    store.accept(request, agent_id="agent-1", invocation_context=None, webhook_cfg=None)
    owner = client.get("/scanner/v1/scans/scan-1/progress", headers={"Authorization": "Bearer owner-token"})
    foreign = client.get("/scanner/v1/scans/scan-1/progress", headers={"Authorization": "Bearer foreign-token"})
    assert owner.status_code == 200
    assert owner.json()["state"] == "queued"
    assert owner.json()["accepted_pack_total"] == 100
    assert foreign.status_code == 404
    assert "secret" not in owner.text


def test_current_identity_is_bounded_and_terminal_timing_freezes(tmp_path):
    now = [100.0]
    store = AcceptedScanStore(tmp_path / "accepted.sqlite3", clock=lambda: now[0])
    request = ScanRequest(
        scan_id="scan-terminal", tenant_id="owner", agent_endpoint="https://agent.invalid",
        credential_ref="secret-ref", corpus_snapshot_hash="sha256:test",
        scanner_version="test", corpus_version="test", seed=0,
        pack_id="starter-100@4", agent_id="agent-1",
    )
    store.accept(request, agent_id="agent-1", invocation_context=None, webhook_cfg=None)
    store.mark_running(request.scan_id)
    now[0] = 110.0
    store.record_case_progress(
        request.scan_id, case_id="case-1", family="safety", state="executing"
    )
    active = store.case_progress(request.scan_id, tenant_id="owner")
    assert active["current_test_id"] == "case-1"
    assert active["current_family"] == "safety"
    now[0] = 120.0
    store.record_case_progress(
        request.scan_id, case_id="case-1", family="safety", state="finished"
    )
    store.mark_terminal(request.scan_id, ScanResult(
        scan_id=request.scan_id, status="complete", scanner_version="test",
        corpus_version="test", rows_emitted=1,
    ))
    frozen = store.case_progress(request.scan_id, tenant_id="owner")
    now[0] = 999.0
    later = store.case_progress(request.scan_id, tenant_id="owner")
    assert later["current_test_id"] is None
    assert later["current_family"] is None
    assert later["timing"] == frozen["timing"]
