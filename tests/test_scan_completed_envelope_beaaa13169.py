"""BEAAA-13169 (§4 P0) — public completed-scan envelope on GET /scan/{scan_id}.

The customer-facing ``GET /scan/{scan_id}`` must return the documented
external result object — the scored envelope a design partner reads at the
finish line of the three-call flow — NOT the internal runtime projection
(``rows_emitted`` / ``spread_per_dim`` / ``dim_results``).

These tests drive ``scan_status_handler`` directly against a runtime with the
score persisted at kickoff (the live edge binds ``scan_log`` +
``score_envelope_factory`` via ``make_default_production_scan_runtime``), so
they exercise the GET-side projection that changed. No scoring path is
re-entered — the envelope is a read-only projection of the already-committed
``score_json`` (N=5 delta = 0 by construction).

Classification per ``contracts/scanner-implementation-supervision-v1.md@v1.0``:
customer-facing response-shape change on the external GET surface. No pillar
weights, no scoring math, no tamper-resistance reduction (read-only
projection). Founder-directed via the 2026-07-10 sync memo §4.
"""
from __future__ import annotations

from typing import Any

from api.scan_handlers import ScanRuntime, scan_status_handler
from api.scan_log import ScanLog
from scanners.live_probe_orchestrator import DimResult, ScanRequest, ScanResult

AGENT_ID = "agent-envelope-1"
TENANT = "tenant-001"
CORPUS_HASH = "sha256:" + "a" * 64

# The external result object a partner reads. Internal runtime fields must
# NEVER appear on the customer envelope.
_EXTERNAL_FIELDS = (
    "are_total",
    "subscores",
    "band",
    "confidence",
    "evidence_refs",
    "uncovered_dimensions",
)
_INTERNAL_ONLY_FIELDS = (
    "rows_emitted",
    "spread_per_dim",
    "dim_results",
    "pillar_spread",
    "total_spread",
    "live_probe_variance_band",
)


def _result(scan_id: str, *, incomplete: bool = False) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status="complete" if not incomplete else "partial",
        scanner_version="0.5.0",
        corpus_version="0.1.0",
        rows_emitted=10,
        k_replicates=5,
        dim_results=(
            DimResult(dim_id="P1.1", raw=4.0, spread=1.0, replicates=(3.0, 4.0, 4.0, 4.0, 5.0)),
            DimResult(dim_id="P2.1", raw=2.5, spread=0.0, replicates=(2.5, 2.5, 2.5, 2.5, 2.5)),
        ),
        spread_per_dim={"P1.1": 1.0, "P2.1": 0.0},
        pillar_spread={"P1": 1.0, "P2": 0.0, "P3": 0.0, "P4": 0.0},
        total_spread=1.0,
        incomplete=incomplete,
    )


def _request(scan_id: str) -> ScanRequest:
    return ScanRequest(
        scan_id=scan_id,
        tenant_id=TENANT,
        agent_endpoint="https://agent.customer.example.com",
        credential_ref="phase1-probe.v1",
        corpus_snapshot_hash=CORPUS_HASH,
        scanner_version="0.5.0",
        corpus_version="0.1.0",
        seed=0,
        agent_id=AGENT_ID,
    )


def _external_score_envelope_factory(uncovered: list[dict[str, Any]] | None = None):
    """Emit a full contract-shaped score envelope, as the live scorer does."""

    def factory(result: ScanResult, agent_id: str, run_timestamp: str) -> dict[str, Any]:
        return {
            "agent_id": agent_id,
            "run_timestamp": run_timestamp,
            "are_total": 99,
            "subscores": {
                "GOVERNANCE": 24.0,
                "ROBUSTNESS": 25.0,
                "BIAS_FAIRNESS": 25.0,
                "OPS": 25.0,
            },
            "band": {"letter": "A", "display": "A+", "modifier": "plus"},
            "band_letter": "A+",
            "confidence": 0.9821,
            "evidence_refs": [
                {"dim_id": "P1.1", "evidence_uri": "https://evidence.local/p11"},
            ],
            "uncovered_dimensions": uncovered if uncovered is not None else [],
            "scanner_version": result.scanner_version,
            "corpus_version": result.corpus_version,
            "model_versions": {"judge": "claude-sonnet-5"},
            "seed": 0,
            "partial_completion": bool(result.incomplete),
            "confidence_below_adjudication_threshold": bool(result.incomplete),
        }

    return factory


def _wired_runtime(scan_id: str, *, factory=None) -> ScanRuntime:
    """Runtime that persists the score at kickoff — mirrors the live edge."""

    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        return _result(scan_request.scan_id, incomplete=False)

    runtime = ScanRuntime(
        run_scan_fn=run_scan_fn,
        scan_log=ScanLog(),
        run_timestamp_factory=lambda: "2026-07-10T12:00:00+00:00",
        score_envelope_factory=factory or _external_score_envelope_factory(),
    )
    runtime.kickoff(_request(scan_id), "scoped-token", agent_id=AGENT_ID)
    return runtime


def test_customer_get_returns_documented_external_envelope():
    runtime = _wired_runtime("scan-ext-1")
    status, payload = scan_status_handler(
        "scan-ext-1", runtime=runtime, audience="customer"
    )
    assert status == 200
    # Every documented external field is present.
    for field_name in _EXTERNAL_FIELDS:
        assert field_name in payload, f"missing external field {field_name!r}"
    assert payload["are_total"] == 99
    assert payload["band"] == "A+"  # letter grade, not the internal dict
    assert payload["subscores"]["GOVERNANCE"] == 24.0
    assert payload["confidence"] == 0.9821
    assert payload["uncovered_dimensions"] == []
    # Reproducibility provenance travels with every reported score.
    assert payload["scanner_version"] == "0.5.0"
    assert payload["corpus_version"] == "0.1.0"
    assert payload["seed"] == 0
    assert payload["model_versions"] == {"judge": "claude-sonnet-5"}


def test_customer_get_omits_internal_runtime_fields():
    runtime = _wired_runtime("scan-ext-2")
    _, payload = scan_status_handler(
        "scan-ext-2", runtime=runtime, audience="customer"
    )
    for field_name in _INTERNAL_ONLY_FIELDS:
        assert field_name not in payload, f"internal field {field_name!r} leaked"


def test_customer_get_surfaces_uncovered_dimensions_on_partial():
    uncovered = [{"dim_id": "P3.2", "reason": "customer_endpoint_timeout"}]

    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        return _result(scan_request.scan_id, incomplete=True)

    runtime = ScanRuntime(
        run_scan_fn=run_scan_fn,
        scan_log=ScanLog(),
        run_timestamp_factory=lambda: "2026-07-10T12:00:00+00:00",
        score_envelope_factory=_external_score_envelope_factory(uncovered=uncovered),
    )
    runtime.kickoff(_request("scan-ext-3"), "scoped-token", agent_id=AGENT_ID)
    _, payload = scan_status_handler(
        "scan-ext-3", runtime=runtime, audience="customer"
    )
    assert payload["uncovered_dimensions"] == uncovered
    assert payload["partial_completion"] is True
    assert payload["confidence_below_adjudication_threshold"] is True


def test_uw_audience_still_gets_full_internal_struct():
    """UW pricing payload is unchanged — retains internal + variance band."""
    runtime = _wired_runtime("scan-ext-4")
    _, payload = scan_status_handler(
        "scan-ext-4", runtime=runtime, audience="uw"
    )
    assert "live_probe_variance_band" in payload
    assert payload["rows_emitted"] == 10


def test_unwired_runtime_falls_back_to_internal_projection():
    """Legacy/unwired runtimes (no persisted score) stay byte-stable."""

    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        return _result(scan_request.scan_id)

    runtime = ScanRuntime(run_scan_fn=run_scan_fn)
    runtime.kickoff(_request("scan-ext-5"), "scoped-token")
    _, payload = scan_status_handler(
        "scan-ext-5", runtime=runtime, audience="customer"
    )
    # No score persisted -> pre-BEAAA-13169 internal shape, variance band
    # still stripped per Δ4.
    assert payload["rows_emitted"] == 10
    assert "live_probe_variance_band" not in payload
    assert "are_total" not in payload
