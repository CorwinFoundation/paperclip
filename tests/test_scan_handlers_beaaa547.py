"""BEAAA-547 (T1) — POST /scan + GET /scan/{scan_id} handlers.

Rule-change per `contracts/scanner-implementation-supervision-v1.md@v1.0`.
``cto_cosign_ref``: BEAAA-531 (CTO ACCEPT) + BEAAA-535 (HoUW countersign on
contract v1.1.0).

AC mapping:

* AC#1 — POST /scan + GET /scan/{scan_id} land in ``api/app.py`` and the
  new handler module.
* AC#3 — Request validation surfaces 422s with the contracted error codes.
* AC#4 — Tenant isolation (T7): ``verify_tenant_isolation`` is the verbatim
  T7 alias; both `/score` and `/scan` yield byte-identical 409s.
* AC#5 — Δ4: ``live_probe_variance_band`` present on UW payload, absent on
  customer payload.
* AC#6 — End-to-end smoke against a synthetic agent endpoint.
* AC#7 — Offline regression (``POST /score`` byte-identical pre/post) —
  verified by the 905-test regression suite running unchanged.
* AC#8 — bin/are-scan + cli_runner.py zero diff (verified at parent close).
"""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from api.scan_handlers import (
    ScanRuntime,
    scan_handler,
    scan_status_handler,
)
from harness import egress
from harness.credentials import CredentialVault
from harness.runner import RunnerError
from scanners.live_probe_orchestrator import (
    CorpusSnapshotMismatchError,
    DEFAULT_K_REPLICATES,
    LIVE_PROBE_VARIANCE_BAND,
    DimResult,
    OrchestratorError,
    ScanRequest,
    ScanResult,
)
from scanners.snapshot_hash import compute_corpus_snapshot_hash


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def corpus_tarball(tmp_path: Path) -> Path:
    path = tmp_path / "corpus.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        data = b'{"corpus_version":"0.1.0"}\n'
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return path


@pytest.fixture
def corpus_hash(corpus_tarball: Path) -> str:
    return compute_corpus_snapshot_hash(corpus_tarball)


def _synthetic_result(scan_id: str, *, incomplete: bool = False) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status="incomplete" if incomplete else "complete",
        scanner_version="0.3.0",
        corpus_version="0.1.0",
        rows_emitted=10,
        k_replicates=5,
        live_probe_variance_band=LIVE_PROBE_VARIANCE_BAND,
        dim_results=(
            DimResult(dim_id="P1.1", raw=4.0, spread=1.0, replicates=(3.0, 4.0, 4.0, 4.0, 5.0)),
            DimResult(dim_id="P2.1", raw=2.5, spread=0.0, replicates=(2.5, 2.5, 2.5, 2.5, 2.5)),
        ),
        spread_per_dim={"P1.1": 1.0, "P2.1": 0.0},
        pillar_spread={"P1": 1.0, "P2": 0.0, "P3": 0.0, "P4": 0.0},
        total_spread=1.0,
        incomplete=incomplete,
        incomplete_reason="variance_band_exceeded_total" if incomplete else None,
    )


@pytest.fixture
def stub_runtime():
    """ScanRuntime whose run_scan_fn returns a fixed synthetic result.

    The handler call still validates everything; the runtime is a stub
    so we don't need a live ModelRunner or corpus tarball in the API tests.
    """
    captured: dict[str, ScanRequest] = {}

    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        captured["request"] = scan_request
        captured["secret_observed"] = secret  # available for assertion
        return _synthetic_result(scan_request.scan_id)

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


# ---------------------------------------------------------------------------
# AC#1 — POST /scan accepts a well-formed request and returns 202.
# ---------------------------------------------------------------------------


def test_post_scan_returns_202_with_scan_id(corpus_hash, stub_runtime):
    body = _good_body(corpus_hash)
    status, payload = scan_handler(
        body,
        tenant_header="tenant-001",
        runtime=stub_runtime,
        scan_id_factory=lambda: "scan-test-1",
    )
    assert status == 202
    # BEAAA-1274 (WS4) AC #5 — additive flags default false on /scan responses.
    assert payload == {
        "scan_id": "scan-test-1",
        "status": "accepted",
        "partial_completion": False,
        "confidence_below_adjudication_threshold": False,
    }


def test_post_scan_surfaces_bounded_partial_completion(corpus_hash):
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del secret
        return _synthetic_result(scan_request.scan_id, incomplete=True)

    runtime = ScanRuntime(run_scan_fn=run_scan_fn)
    status, payload = scan_handler(
        _good_body(corpus_hash),
        tenant_header="tenant-001",
        runtime=runtime,
        scan_id_factory=lambda: "scan-partial-7887",
    )

    assert status == 202
    assert payload == {
        "scan_id": "scan-partial-7887",
        "status": "partial",
        "partial_completion": True,
        "confidence_below_adjudication_threshold": True,
    }


def test_get_scan_returns_404_for_unknown_id(stub_runtime):
    with pytest.raises(Exception) as exc_info:
        scan_status_handler("nonexistent-scan", runtime=stub_runtime)
    assert exc_info.value.status == 404
    assert exc_info.value.message == "scan_not_found"


def test_get_scan_returns_customer_payload_after_post(corpus_hash, stub_runtime):
    scan_handler(
        _good_body(corpus_hash),
        tenant_header="tenant-001",
        runtime=stub_runtime,
        scan_id_factory=lambda: "scan-test-2",
    )
    status, payload = scan_status_handler("scan-test-2", runtime=stub_runtime)
    assert status == 200
    assert payload["scan_id"] == "scan-test-2"


def test_scan_runtime_rejects_invalid_in_memory_state_ttl():
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del secret
        return _synthetic_result(scan_request.scan_id)

    with pytest.raises(ValueError, match="in_memory_state_ttl_seconds"):
        ScanRuntime(run_scan_fn=run_scan_fn, in_memory_state_ttl_seconds=0)


def test_scan_runtime_in_memory_state_expires_after_ttl(corpus_hash):
    now = [100.0]

    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del secret
        return _synthetic_result(scan_request.scan_id)

    runtime = ScanRuntime(
        run_scan_fn=run_scan_fn,
        in_memory_state_ttl_seconds=10,
        monotonic_clock=lambda: now[0],
    )
    scan_handler(
        _good_body(corpus_hash, agent_id="agent-ttl"),
        tenant_header="tenant-001",
        runtime=runtime,
        scan_id_factory=lambda: "scan-ttl",
    )

    assert runtime.fetch("scan-ttl") is not None
    assert runtime.current_scan_request("scan-ttl") is not None
    assert runtime.has_prior_scan_for_agent("agent-ttl") is True

    now[0] = 111.0

    assert runtime.fetch("scan-ttl") is None
    assert runtime.current_scan_request("scan-ttl") is None
    assert runtime.has_prior_scan_for_agent("agent-ttl") is False


def test_scan_runtime_ttl_keeps_scan_log_prior_scan(corpus_hash, tmp_path):
    from api.scan_log import ScanLog

    now = [100.0]

    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del secret
        return _synthetic_result(scan_request.scan_id)

    scan_log = ScanLog(tmp_path / "scan-log.sqlite3")
    scan_log.append_scan(
        agent_id="agent-persisted",
        run_timestamp="2026-07-04T00:00:00Z",
        scan_id="scan-persisted",
        scanner_version="0.4.2",
        corpus_version="0.2.0",
        score_json={"are_total": 91, "band": "A"},
        forensic_bundle_uri="s3://bundles/scan-persisted",
    )
    runtime = ScanRuntime(
        run_scan_fn=run_scan_fn,
        scan_log=scan_log,
        in_memory_state_ttl_seconds=10,
        monotonic_clock=lambda: now[0],
    )
    runtime.register_prior_scan("agent-ephemeral")

    now[0] = 111.0

    assert runtime.has_prior_scan_for_agent("agent-ephemeral") is False
    assert runtime.has_prior_scan_for_agent("agent-persisted") is True


# ---------------------------------------------------------------------------
# AC#3 — request validation produces clear error codes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_field", [
    "tenant_id",
    "agent_endpoint",
    "credential_ref",
    "credential_secret",
    "corpus_snapshot_hash",
    "scanner_version",
    "corpus_version",
])
def test_missing_required_field_yields_422(corpus_hash, stub_runtime, missing_field):
    body = _good_body(corpus_hash)
    body.pop(missing_field)
    with pytest.raises(Exception) as exc_info:
        scan_handler(body, tenant_header="tenant-001", runtime=stub_runtime)
    assert exc_info.value.status == 422
    assert exc_info.value.message == f"{missing_field}_required"


def test_invalid_scan_mode_yields_422(corpus_hash, stub_runtime):
    body = _good_body(corpus_hash, scan_mode="offline_replay")
    with pytest.raises(Exception) as exc_info:
        scan_handler(body, tenant_header="tenant-001", runtime=stub_runtime)
    assert exc_info.value.status == 422
    assert exc_info.value.message == "scan_mode_invalid"


def test_live_probe_refuses_residential_proxy_env(corpus_hash, stub_runtime, monkeypatch):
    monkeypatch.setenv(egress.PROXY_ENV, "http://u:p@gate.example.com:7000")
    body = _good_body(corpus_hash)
    with pytest.raises(Exception) as exc_info:
        scan_handler(body, tenant_header="tenant-001", runtime=stub_runtime)
    assert exc_info.value.status == 422
    assert exc_info.value.message == "residential_proxy_forbidden_for_live_probe"
    assert "request" not in stub_runtime.captured


def test_live_probe_refuses_unverified_default_deny(corpus_hash, stub_runtime, monkeypatch):
    def fail_verifier(**_kwargs):
        raise egress.LiveEgressPostureError("default deny not configured")

    monkeypatch.setattr(egress, "verify_live_egress_fail_closed", fail_verifier)
    body = _good_body(corpus_hash)
    with pytest.raises(Exception) as exc_info:
        scan_handler(body, tenant_header="tenant-001", runtime=stub_runtime)

    assert exc_info.value.status == 422
    assert exc_info.value.message == "live_egress_default_deny_unverified"
    assert "request" not in stub_runtime.captured


@pytest.mark.parametrize("bad_k", [1, 3, 7, 100, "5", True])
def test_invalid_k_replicates_yields_422(corpus_hash, stub_runtime, bad_k):
    body = _good_body(corpus_hash, k_replicates=bad_k)
    with pytest.raises(Exception) as exc_info:
        scan_handler(body, tenant_header="tenant-001", runtime=stub_runtime)
    assert exc_info.value.status == 422
    assert exc_info.value.message == "k_replicates_invalid"


def test_invalid_corpus_snapshot_hash_yields_422(corpus_hash, stub_runtime):
    body = _good_body(corpus_hash, corpus_snapshot_hash="not-a-sha256")
    with pytest.raises(Exception) as exc_info:
        scan_handler(body, tenant_header="tenant-001", runtime=stub_runtime)
    assert exc_info.value.status == 422
    assert exc_info.value.message == "corpus_snapshot_hash_invalid"


def test_invalid_agent_endpoint_host_yields_422(corpus_hash, stub_runtime):
    body = _good_body(corpus_hash, agent_endpoint={"host": "not a valid hostname!!"})
    with pytest.raises(Exception) as exc_info:
        scan_handler(body, tenant_header="tenant-001", runtime=stub_runtime)
    assert exc_info.value.status == 422
    assert exc_info.value.message == "agent_endpoint_host_invalid"


def test_invalid_credential_ref_yields_422(corpus_hash, stub_runtime):
    body = _good_body(corpus_hash, credential_ref="contains spaces & special!")
    with pytest.raises(Exception) as exc_info:
        scan_handler(body, tenant_header="tenant-001", runtime=stub_runtime)
    assert exc_info.value.status == 422
    assert exc_info.value.message == "credential_ref_invalid"


def test_wrapped_corpus_snapshot_mismatch_yields_422(corpus_hash):
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del scan_request, secret
        cause = CorpusSnapshotMismatchError(
            "corpus_snapshot_mismatch: expected pinned hash"
        )
        raise OrchestratorError(
            "prober subprocess raised CorpusSnapshotMismatchError"
        ) from cause

    runtime = ScanRuntime(run_scan_fn=run_scan_fn)
    with pytest.raises(Exception) as exc_info:
        scan_handler(
            _good_body(corpus_hash),
            tenant_header="tenant-001",
            runtime=runtime,
            scan_id_factory=lambda: "wrapped-corpus-mismatch",
        )
    assert exc_info.value.status == 422
    assert exc_info.value.message == "corpus_snapshot_mismatch"


def test_serialized_corpus_snapshot_mismatch_yields_422(corpus_hash):
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del scan_request, secret
        raise OrchestratorError(
            "prober subprocess raised CorpusSnapshotMismatchError: "
            "corpus_snapshot_mismatch: expected pinned hash"
        )

    runtime = ScanRuntime(run_scan_fn=run_scan_fn)
    with pytest.raises(Exception) as exc_info:
        scan_handler(
            _good_body(corpus_hash),
            tenant_header="tenant-001",
            runtime=runtime,
            scan_id_factory=lambda: "serialized-corpus-mismatch",
        )
    assert exc_info.value.status == 422
    assert exc_info.value.message == "corpus_snapshot_mismatch"


def test_wrapped_customer_runner_dns_error_yields_422(corpus_hash):
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del scan_request, secret
        cause = RunnerError(
            "customer endpoint transport error: [Errno -2] Name or service not known"
        )
        raise OrchestratorError("prober subprocess raised RunnerError") from cause

    runtime = ScanRuntime(run_scan_fn=run_scan_fn)
    with pytest.raises(Exception) as exc_info:
        scan_handler(
            _good_body(corpus_hash),
            tenant_header="tenant-001",
            runtime=runtime,
            scan_id_factory=lambda: "wrapped-runner-dns",
        )
    assert exc_info.value.status == 422
    assert exc_info.value.message == "customer_endpoint_unreachable"


def test_serialized_customer_runner_dns_error_yields_422(corpus_hash):
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del scan_request, secret
        raise OrchestratorError(
            "prober subprocess raised RunnerError: "
            "customer endpoint transport error: [Errno -2] Name or service not known"
        )

    runtime = ScanRuntime(run_scan_fn=run_scan_fn)
    with pytest.raises(Exception) as exc_info:
        scan_handler(
            _good_body(corpus_hash),
            tenant_header="tenant-001",
            runtime=runtime,
            scan_id_factory=lambda: "serialized-runner-dns",
        )
    assert exc_info.value.status == 422
    assert exc_info.value.message == "customer_endpoint_unreachable"


def test_wrapped_customer_runner_timeout_yields_504(corpus_hash):
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del scan_request, secret
        cause = RunnerError("customer endpoint timeout after 30.0s")
        raise OrchestratorError("prober subprocess raised RunnerError") from cause

    runtime = ScanRuntime(run_scan_fn=run_scan_fn)
    with pytest.raises(Exception) as exc_info:
        scan_handler(
            _good_body(corpus_hash),
            tenant_header="tenant-001",
            runtime=runtime,
            scan_id_factory=lambda: "wrapped-runner-timeout",
    )
    assert exc_info.value.status == 504
    assert exc_info.value.message == "customer_endpoint_unreachable_timeout"


def test_serialized_customer_runner_transport_timeout_yields_504(corpus_hash):
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del scan_request, secret
        raise OrchestratorError(
            "prober subprocess raised RunnerError: "
            "customer endpoint transport error: ReadTimeout: request timed out"
        )

    runtime = ScanRuntime(run_scan_fn=run_scan_fn)
    with pytest.raises(Exception) as exc_info:
        scan_handler(
            _good_body(corpus_hash),
            tenant_header="tenant-001",
            runtime=runtime,
            scan_id_factory=lambda: "serialized-runner-transport-timeout",
    )
    assert exc_info.value.status == 504
    assert exc_info.value.message == "customer_endpoint_unreachable_timeout"


@pytest.mark.parametrize(
    ("runner_message", "expected_status", "expected_detail"),
    (
        (
            "customer endpoint transport error: connection reset by peer",
            504,
            "customer_endpoint_unreachable_transport",
        ),
        (
            "customer endpoint transport error: TLS handshake failed",
            504,
            "customer_endpoint_unreachable_transport",
        ),
        (
            "customer endpoint transport error: SSL certificate verify failed",
            504,
            "customer_endpoint_unreachable_transport",
        ),
        (
            "customer endpoint http error: HTTP 401",
            502,
            "customer_endpoint_http_error",
        ),
        (
            "customer endpoint HTTP 404: Not Found",
            422,
            "customer_endpoint_route_not_found",
        ),
        (
            "syllable start_session HTTP 404: Not Found",
            422,
            "customer_endpoint_route_not_found",
        ),
        (
            "customer endpoint HTTP 503: Service Unavailable",
            502,
            "customer_endpoint_http_error",
        ),
        (
            "syllable send_message request failed: 522 Server Error",
            504,
            "customer_endpoint_unreachable_transport",
        ),
        (
            "syllable start_session request failed: "
            "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol",
            504,
            "customer_endpoint_unreachable_transport",
        ),
        (
            "syllable send_message request failed: connection reset by peer",
            504,
            "customer_endpoint_unreachable_transport",
        ),
        (
            "syllable scan deadline exceeded",
            504,
            "customer_endpoint_unreachable_transport",
        ),
        (
            "syllable start_session request failed: [Errno -2] Name or service not known",
            422,
            "customer_endpoint_unreachable",
        ),
    ),
)
def test_serialized_customer_runner_adjacent_transport_classes(
    corpus_hash,
    runner_message,
    expected_status,
    expected_detail,
):
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del scan_request, secret
        raise OrchestratorError(
            f"prober subprocess raised RunnerError: {runner_message}"
        )

    runtime = ScanRuntime(run_scan_fn=run_scan_fn)
    with pytest.raises(Exception) as exc_info:
        scan_handler(
            _good_body(corpus_hash),
            tenant_header="tenant-001",
            runtime=runtime,
            scan_id_factory=lambda: "serialized-runner-adjacent",
        )
    assert exc_info.value.status == expected_status
    assert exc_info.value.message == expected_detail


def test_wrapped_internal_orchestrator_error_stays_500(corpus_hash):
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del scan_request, secret
        cause = RunnerError("OPENAI_API_KEY not set")
        raise OrchestratorError("prober subprocess raised RunnerError") from cause

    runtime = ScanRuntime(run_scan_fn=run_scan_fn)
    with pytest.raises(Exception) as exc_info:
        scan_handler(
            _good_body(corpus_hash),
            tenant_header="tenant-001",
            runtime=runtime,
            scan_id_factory=lambda: "wrapped-internal-runner-error",
        )
    assert exc_info.value.status == 500
    assert exc_info.value.message == "orchestrator_error"


def test_serialized_internal_runner_error_stays_500(corpus_hash):
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del scan_request, secret
        raise OrchestratorError(
            "prober subprocess raised RunnerError: OPENAI_API_KEY not set"
        )

    runtime = ScanRuntime(run_scan_fn=run_scan_fn)
    with pytest.raises(Exception) as exc_info:
        scan_handler(
            _good_body(corpus_hash),
            tenant_header="tenant-001",
            runtime=runtime,
            scan_id_factory=lambda: "serialized-internal-runner-error",
        )
    assert exc_info.value.status == 500
    assert exc_info.value.message == "orchestrator_error"


# ---------------------------------------------------------------------------
# AC#4 — tenant isolation reuses the verbatim T7 alias.
# ---------------------------------------------------------------------------


def test_missing_tenant_header_yields_409(corpus_hash, stub_runtime):
    body = _good_body(corpus_hash)
    with pytest.raises(Exception) as exc_info:
        scan_handler(body, tenant_header=None, runtime=stub_runtime)
    assert exc_info.value.status == 409


def test_mismatched_tenant_yields_409(corpus_hash, stub_runtime):
    body = _good_body(corpus_hash, tenant_id="tenant-A")
    with pytest.raises(Exception) as exc_info:
        scan_handler(body, tenant_header="tenant-B", runtime=stub_runtime)
    assert exc_info.value.status == 409


def test_scan_handler_uses_verbatim_tenant_isolation_alias():
    """Sentry: the alias from BEAAA-546 is the single source of truth."""
    from api import handlers as h
    from api import scan_handlers as sh

    assert sh.verify_tenant_isolation is h.verify_tenant_isolation
    assert h.verify_tenant_isolation is h._verify_tenant_isolation


# ---------------------------------------------------------------------------
# AC#5 — Δ4: live_probe_variance_band absent on customer payload, present on UW.
# ---------------------------------------------------------------------------


def test_customer_payload_strips_live_probe_variance_band(corpus_hash, stub_runtime):
    scan_handler(
        _good_body(corpus_hash),
        tenant_header="tenant-001",
        runtime=stub_runtime,
        scan_id_factory=lambda: "scan-d4",
    )
    _, customer = scan_status_handler("scan-d4", runtime=stub_runtime, audience="customer")
    assert "live_probe_variance_band" not in customer


def test_uw_payload_retains_live_probe_variance_band(corpus_hash, stub_runtime):
    scan_handler(
        _good_body(corpus_hash),
        tenant_header="tenant-001",
        runtime=stub_runtime,
        scan_id_factory=lambda: "scan-d4-uw",
    )
    _, uw = scan_status_handler("scan-d4-uw", runtime=stub_runtime, audience="uw")
    assert uw["live_probe_variance_band"] == LIVE_PROBE_VARIANCE_BAND == 2


def test_invalid_audience_yields_422(corpus_hash, stub_runtime):
    scan_handler(
        _good_body(corpus_hash),
        tenant_header="tenant-001",
        runtime=stub_runtime,
        scan_id_factory=lambda: "scan-aud",
    )
    with pytest.raises(Exception) as exc_info:
        scan_status_handler("scan-aud", runtime=stub_runtime, audience="random")
    assert exc_info.value.status == 422
    assert exc_info.value.message == "audience_invalid"


# ---------------------------------------------------------------------------
# BEAAA-11858 — tenant isolation + audience entitlement on GET /scan/{scan_id}.
# Pure access control; no scoring path touched (golden N=5 delta = 0).
# ---------------------------------------------------------------------------


def _register_scan(corpus_hash, runtime, scan_id, *, tenant_id):
    scan_handler(
        _good_body(corpus_hash, tenant_id=tenant_id),
        tenant_header=tenant_id,
        runtime=runtime,
        scan_id_factory=lambda: scan_id,
    )


def test_cross_tenant_read_returns_404_not_403(corpus_hash, stub_runtime):
    # scan owned by tenant-001; a different authenticated tenant must not
    # learn it exists — 404 (existence-oracle avoidance), never 403.
    _register_scan(corpus_hash, stub_runtime, "scan-iso", tenant_id="tenant-001")
    with pytest.raises(Exception) as exc_info:
        scan_status_handler(
            "scan-iso",
            runtime=stub_runtime,
            authenticated_tenant_id="tenant-999",
        )
    assert exc_info.value.status == 404
    assert exc_info.value.message == "scan_not_found"


def test_same_tenant_read_succeeds(corpus_hash, stub_runtime):
    _register_scan(corpus_hash, stub_runtime, "scan-own", tenant_id="tenant-001")
    status, payload = scan_status_handler(
        "scan-own",
        runtime=stub_runtime,
        authenticated_tenant_id="tenant-001",
    )
    assert status == 200
    assert payload["scan_id"] == "scan-own"


def test_none_auth_context_preserves_open_read(corpus_hash, stub_runtime):
    # Local/test no-auth path (authenticated_tenant_id=None) is byte-stable:
    # no tenant scoping applied, matching pre-BEAAA-11858 behavior.
    _register_scan(corpus_hash, stub_runtime, "scan-open", tenant_id="tenant-001")
    status, payload = scan_status_handler("scan-open", runtime=stub_runtime)
    assert status == 200
    assert payload["scan_id"] == "scan-open"


def test_uw_audience_forbidden_when_not_entitled(corpus_hash, stub_runtime):
    # Caller owns the scan (passes tenant isolation) but is not uw-entitled:
    # requesting the pricing-internal payload is 403, not a self-elevation.
    _register_scan(corpus_hash, stub_runtime, "scan-uw-deny", tenant_id="tenant-001")
    with pytest.raises(Exception) as exc_info:
        scan_status_handler(
            "scan-uw-deny",
            runtime=stub_runtime,
            audience="uw",
            authenticated_tenant_id="tenant-001",
            uw_entitled=False,
        )
    assert exc_info.value.status == 403
    assert exc_info.value.message == "audience_forbidden"


def test_uw_audience_allowed_when_entitled(corpus_hash, stub_runtime):
    _register_scan(corpus_hash, stub_runtime, "scan-uw-ok", tenant_id="tenant-001")
    status, payload = scan_status_handler(
        "scan-uw-ok",
        runtime=stub_runtime,
        audience="uw",
        authenticated_tenant_id="tenant-001",
        uw_entitled=True,
    )
    assert status == 200
    assert payload["live_probe_variance_band"] == LIVE_PROBE_VARIANCE_BAND


def test_cross_tenant_uw_returns_404_not_403(corpus_hash, stub_runtime):
    # Tenant isolation is enforced BEFORE the audience gate: a cross-tenant
    # probe with ?audience=uw returns 404, never a 403 that would confirm
    # the scan_id exists under another tenant.
    _register_scan(corpus_hash, stub_runtime, "scan-uw-iso", tenant_id="tenant-001")
    with pytest.raises(Exception) as exc_info:
        scan_status_handler(
            "scan-uw-iso",
            runtime=stub_runtime,
            audience="uw",
            authenticated_tenant_id="tenant-999",
            uw_entitled=False,
        )
    assert exc_info.value.status == 404
    assert exc_info.value.message == "scan_not_found"


# ---------------------------------------------------------------------------
# AC#6 — end-to-end smoke against the real orchestrator + synthetic endpoint.
# ---------------------------------------------------------------------------


def test_end_to_end_smoke_with_real_orchestrator(corpus_tarball, corpus_hash, tmp_path):
    """Wire the real orchestrator behind ScanRuntime against a mock probe_run_fn.

    The handler validates, builds a ScanRequest, calls runtime.kickoff which
    invokes the real ``run_scan`` with the synthetic probe stream. The
    scan completes cleanly (no cap breach, spread within band).
    """
    from harness.runner import MockRunner
    from scanners.live_probe_orchestrator import run_scan
    from scanners.live_probe_workers import WorkerChannel

    vault = CredentialVault()

    def real_run_scan(scan_request: ScanRequest, secret: str) -> ScanResult:
        vault.register(scan_request.credential_ref, secret)
        channel = WorkerChannel(max_queue=256)
        return run_scan(
            scan_request,
            vault,
            channel,
            corpus_tarball_path=corpus_tarball,
            probe_run_fn=lambda _r: (
                ("P1.1", {"raw": 3.0,
                          "evidence_uri": "https://evidence.local/p11",
                          "evidence_sha256": "sha256:" + "a" * 64}),
                ("P2.1", {"raw": 2.5,
                          "evidence_uri": "https://evidence.local/p21",
                          "evidence_sha256": "sha256:" + "b" * 64}),
            ),
            runner=MockRunner("e2e", lambda p: "ok"),
        )

    runtime = ScanRuntime(run_scan_fn=real_run_scan)
    try:
        body = _good_body(corpus_hash)
        status, post_payload = scan_handler(
            body,
            tenant_header="tenant-001",
            runtime=runtime,
            scan_id_factory=lambda: "smoke-1",
        )
        assert status == 202
        get_status, get_payload = scan_status_handler("smoke-1", runtime=runtime)
        assert get_status == 200
        assert get_payload["status"] == "complete"
        assert get_payload["rows_emitted"] == 10  # 5 replicates × 2 dims
        assert get_payload["incomplete"] is False
    finally:
        vault.forget_all()


# ---------------------------------------------------------------------------
# Hard Invariants verification.
# ---------------------------------------------------------------------------


def test_scan_handler_module_does_not_import_score_handler():
    """Hard Invariant #2: the scan handler does not import the offline
    score-path scoring logic. Parse the module's AST and confirm
    score_handler is not in any imported name.
    """
    import ast

    src = Path(__import__("api.scan_handlers").scan_handlers.__file__).read_text()
    tree = ast.parse(src)
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)
    assert "score_handler" not in imported_names, (
        f"scan_handlers.py imports score_handler — Hard Invariant #2 risk. "
        f"Imported names: {sorted(imported_names)}"
    )


def test_scan_runtime_drops_local_secret_after_kickoff(corpus_hash):
    """Hard Invariant #4: the handler does not retain the credential_secret
    after calling runtime.kickoff. (Verified by inspecting that the only
    persistence path is the runtime.results dict which only stores ScanResult.)
    """
    secret_observed: dict[str, str] = {}

    def run_scan_fn(req: ScanRequest, secret: str) -> ScanResult:
        secret_observed["value"] = secret
        return _synthetic_result(req.scan_id)

    runtime = ScanRuntime(run_scan_fn=run_scan_fn)
    body = _good_body(corpus_hash)
    scan_handler(
        body,
        tenant_header="tenant-001",
        runtime=runtime,
        scan_id_factory=lambda: "secret-drop",
    )
    # The secret reached run_scan_fn (so the runtime can register it),
    # but the runtime.results dict carries ScanResult only — no secret.
    result = runtime.fetch("secret-drop")
    assert result is not None
    # ScanResult fields do not include the secret.
    for value in result.__dict__.values() if hasattr(result, "__dict__") else []:
        assert secret_observed["value"] not in repr(value)
