"""BEAAA-42189: create spend cap is enforced before execution."""
from __future__ import annotations

import pytest

from api.scan_handlers import HandlerError, ScanRuntime, scan_handler
from scanners.live_probe_orchestrator import ScanResult


def _body(**overrides):
    body = {
        "tenant_id": "tenant-001",
        "agent_endpoint": {"host": "agent.customer.example.com"},
        "credential_ref": "vapi-test-assistant",
        "credential_secret": "test-only-secret",
        "corpus_snapshot_hash": "sha256:" + "a" * 64,
        "scanner_version": "test",
        "corpus_version": "test",
        "seed": 42,
        "scan_mode": "live_probe",
        "k_replicates": 5,
    }
    body.update(overrides)
    return body


def _runtime(captured):
    def run(request, secret):
        captured.append((request, secret))
        return ScanResult(
            scan_id=request.scan_id, status="complete",
            scanner_version=request.scanner_version,
            corpus_version=request.corpus_version, rows_emitted=0,
            k_replicates=request.k_replicates,
        )
    return ScanRuntime(run_scan_fn=run)


@pytest.mark.parametrize("value,message", [
    (0, "max_cost_usd_invalid"),
    (True, "max_cost_usd_invalid"),
    (float("inf"), "max_cost_usd_invalid"),
    (10.01, "max_cost_usd_exceeds_server_limit"),
])
def test_invalid_cap_rejected_before_runtime(monkeypatch, value, message):
    monkeypatch.setenv("BACKBOND_SCAN_MAX_CREATE_COST_USD", "10")
    monkeypatch.setenv("BACKBOND_SCAN_DEFAULT_MAX_COST_USD", "10")
    captured = []
    body = _body()
    if value is not None:
        body["max_cost_usd"] = value
    with pytest.raises(HandlerError) as exc_info:
        scan_handler(body, tenant_header="tenant-001", runtime=_runtime(captured))
    assert (exc_info.value.status, exc_info.value.message) == (422, message)
    assert captured == []


def test_ten_dollar_cap_reaches_runtime(monkeypatch):
    monkeypatch.setenv("BACKBOND_SCAN_MAX_CREATE_COST_USD", "10")
    monkeypatch.setenv("BACKBOND_SCAN_DEFAULT_MAX_COST_USD", "10")
    captured = []
    status, _ = scan_handler(
        _body(max_cost_usd=10), tenant_header="tenant-001",
        runtime=_runtime(captured), scan_id_factory=lambda: "scan-capped",
    )
    assert status == 202
    assert captured[0][0].max_cost_usd == 10.0


def test_omitted_cap_uses_deployment_default(monkeypatch):
    monkeypatch.setenv("BACKBOND_SCAN_MAX_CREATE_COST_USD", "250")
    monkeypatch.setenv("BACKBOND_SCAN_DEFAULT_MAX_COST_USD", "125")
    captured = []
    status, _ = scan_handler(
        _body(), tenant_header="tenant-001",
        runtime=_runtime(captured), scan_id_factory=lambda: "scan-default-capped",
    )
    assert status == 202
    assert captured[0][0].max_cost_usd == 125.0


@pytest.mark.parametrize(
    ("default", "maximum"),
    [("bad", "10"), ("10", "bad"), ("0", "10"), ("11", "10")],
)
def test_invalid_deployment_configuration_fails_closed(monkeypatch, default, maximum):
    monkeypatch.setenv("BACKBOND_SCAN_DEFAULT_MAX_COST_USD", default)
    monkeypatch.setenv("BACKBOND_SCAN_MAX_CREATE_COST_USD", maximum)
    with pytest.raises(RuntimeError, match="invalid Scanner create cost configuration"):
        scan_handler(
            _body(), tenant_header="tenant-001", runtime=_runtime([])
        )
