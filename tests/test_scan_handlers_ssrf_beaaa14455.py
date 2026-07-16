"""BEAAA-14455 (F5) SSRF boundary hardening for agent_endpoint.host.

Rule-execution / input-acceptance hardening under
contracts/are-scanner-api-v1.md@v1.0. The wired egress layer remains the
resolve-time control; these tests cover literal-host rejection at API intake.
"""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from api.handlers import HandlerError
from api.scan_handlers import (
    ScanRuntime,
    _validate_agent_endpoint,
    _validate_https_url_or_host_safety,
    scan_handler,
)
from harness import egress
from scanners.live_probe_orchestrator import LIVE_PROBE_VARIANCE_BAND, DimResult
from scanners.live_probe_orchestrator import ScanRequest, ScanResult
from scanners.snapshot_hash import compute_corpus_snapshot_hash


@pytest.fixture
def corpus_hash(tmp_path: Path) -> str:
    path = tmp_path / "corpus.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        data = b'{"corpus_version":"0.1.0"}\n'
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return compute_corpus_snapshot_hash(path)


@pytest.fixture
def stub_runtime():
    captured: dict[str, object] = {}

    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        captured["request"] = scan_request
        captured["secret_observed"] = secret
        return ScanResult(
            scan_id=scan_request.scan_id,
            status="complete",
            scanner_version="0.3.0",
            corpus_version="0.1.0",
            rows_emitted=10,
            k_replicates=5,
            live_probe_variance_band=LIVE_PROBE_VARIANCE_BAND,
            dim_results=(
                DimResult(dim_id="P1.1", raw=4.0, spread=0.0, replicates=(4.0,) * 5),
            ),
            spread_per_dim={"P1.1": 0.0},
            pillar_spread={"P1": 0.0, "P2": 0.0, "P3": 0.0, "P4": 0.0},
            total_spread=0.0,
            incomplete=False,
            incomplete_reason=None,
        )

    runtime = ScanRuntime(run_scan_fn=run_scan_fn)
    runtime.captured = captured  # type: ignore[attr-defined]
    return runtime


def _good_body(corpus_hash: str, **overrides) -> dict:
    base = {
        "tenant_id": "tenant-001",
        "agent_endpoint": {"host": "agent.customer.example.com"},
        "credential_ref": "phase1-probe.v1",
        "credential_secret": "scoped-token-abc",
        "corpus_snapshot_hash": corpus_hash,
        "scanner_version": "0.3.0",
        "corpus_version": "0.1.0",
        "seed": 42,
        "scan_mode": "live_probe",
        "k_replicates": 5,
        "requested_pillars": ["GOVERNANCE", "ROBUSTNESS"],
    }
    base.update(overrides)
    return base


def test_url_or_host_safety_reuses_egress_predicate(monkeypatch):
    calls: list[str] = []
    original = egress._is_forbidden_resolved_ip

    def recorder(address: str) -> bool:
        calls.append(address)
        return original(address)

    monkeypatch.setattr(egress, "_is_forbidden_resolved_ip", recorder)

    with pytest.raises(HandlerError) as exc_info:
        _validate_https_url_or_host_safety(
            "https://169.254.169.254/latest/meta-data",
            error_code="boundary_host_forbidden",
            allow_private_ip_literals=True,
            require_https_url=True,
        )

    assert exc_info.value.status == 422
    assert exc_info.value.message == "boundary_host_forbidden"
    assert calls == ["169.254.169.254"]


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1/hook",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/hook",
    ],
)
def test_url_or_host_safety_rejects_forbidden_ip_literals(value):
    with pytest.raises(HandlerError) as exc_info:
        _validate_https_url_or_host_safety(
            value,
            error_code="boundary_host_forbidden",
            allow_private_ip_literals=True,
            require_https_url=True,
        )

    assert exc_info.value.status == 422
    assert exc_info.value.message == "boundary_host_forbidden"


def test_url_or_host_safety_requires_https_when_url_is_required():
    with pytest.raises(HandlerError) as exc_info:
        _validate_https_url_or_host_safety(
            "http://agent.customer.example.com/hook",
            error_code="boundary_host_forbidden",
            require_https_url=True,
        )

    assert exc_info.value.status == 422
    assert exc_info.value.message == "boundary_host_forbidden"


@pytest.mark.parametrize(
    "value, expected_host",
    [
        ("agent.customer.example.com", "agent.customer.example.com"),
        ("10.0.3.14", "10.0.3.14"),
        ("https://10.0.3.14/hook", "10.0.3.14"),
        ("https://agent.customer.example.com/hook", "agent.customer.example.com"),
    ],
)
def test_url_or_host_safety_accepts_public_and_vpc_private_hosts(value, expected_host):
    assert (
        _validate_https_url_or_host_safety(
            value,
            error_code="boundary_host_forbidden",
            allow_private_ip_literals=True,
        )
        == expected_host
    )


@pytest.mark.parametrize("bad_host", ["127.0.0.1", "169.254.169.254"])
def test_agent_endpoint_forbidden_literal_host_rejected(bad_host):
    with pytest.raises(HandlerError) as exc_info:
        _validate_agent_endpoint({"host": bad_host})

    assert exc_info.value.status == 422
    assert exc_info.value.message == "agent_endpoint_host_forbidden"


def test_agent_endpoint_vpc_private_host_still_accepted():
    assert _validate_agent_endpoint({"host": "10.0.3.14"}) == {
        "host": "10.0.3.14",
        "transport": "rest",
    }


def test_agent_endpoint_public_hostname_still_returns_202(corpus_hash, stub_runtime):
    status, payload = scan_handler(
        _good_body(corpus_hash),
        tenant_header="tenant-001",
        runtime=stub_runtime,
        scan_id_factory=lambda: "scan-beaaa14455",
    )

    assert status == 202
    assert payload["scan_id"] == "scan-beaaa14455"
    request = stub_runtime.captured["request"]
    assert request.agent_endpoint == "https://agent.customer.example.com"
