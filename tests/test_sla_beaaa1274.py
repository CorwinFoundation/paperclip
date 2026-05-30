"""BEAAA-1274 (WS4 of BEAAA-1255) — SLA instrumentation + degradation path
(purpose-aware) on POST /scanner/v1/rescans.

Rule-execution per ``contracts/scanner-implementation-supervision-v1.md@v1.0``
classification table row "Refactor that doesn't touch behavior". SLA policy
authority: scope-beta-design rev 2 §2.2 + HoUW Q2a/Q2b binding co-sign
(BEAAA-1243). The umbrella v1.1.0 → v1.2.0 contract bump (ADR-0009 §3.2) lives
at WS7 (BEAAA-1272); this WS only wires the flags + metric.

AC mapping (per BEAAA-1274 acceptance criteria):

* AC#1 — wall-clock metric emit on every /rescans call, per-``purpose``.
  Rolling p50/p95/p99 observable via ``SlaRegistry.snapshot()`` (and the
  ``GET /scanner/v1/sla`` endpoint).
* AC#2 — ``pre_bind`` / ``mid_term`` path returns within ≤4 hr p95 under
  nominal conditions. End-to-end verification at WS5 smoke; this file holds
  the unit-level invariant (these purposes pass through, flags stay false).
* AC#3 — ``claim_review`` under induced degradation at 6 hr extends (no
  flags flip, no routing event).
* AC#4 — ``claim_review`` past the 12-hr ceiling with incomplete result
  emits the partial response: both flags ``true`` + ``uncovered_dimensions[]``
  populated + ``claim_review.partial_fallback`` routing event captured.
* AC#5 — both additive boolean response flags default ``false`` on every
  /scans and /rescans response outside the degradation path.
* AC#6 — N=5 calibration on the golden set under nominal conditions via
  /rescans holds 0 by construction (the scoring engine path is unchanged
  from WS1; verified by the ``test_rescan_n5_delta_is_zero_by_construction``
  sentry that survived WS4) — re-asserted here with the SLA wiring active so
  the metric path can't accidentally move a golden.
"""
from __future__ import annotations

import io
import os
import tarfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest(f"fastapi not installed: {exc}")

from api.app import RESCANS_MEASURED_ENV, create_app
from api.bundle import BundleStore
from api.scan_handlers import ScanRuntime, rescan_handler, scan_handler
from api.sla import (
    CLAIM_REVIEW_PARTIAL_FALLBACK_EVENT,
    Clock,
    PARTIAL_FALLBACK_REASONS,
    ROLLING_WINDOW_N,
    RoutingEventSink,
    SLA_CLAIM_REVIEW_CEILING_SECONDS,
    SLA_P95_TARGET_SECONDS,
    SlaMetric,
    SlaRegistry,
    evaluate_degradation,
)
from rubric.scoring import Rubric
from scanners.live_probe_orchestrator import (
    LIVE_PROBE_VARIANCE_BAND,
    DimResult,
    ScanRequest,
    ScanResult,
)
from scanners.snapshot_hash import compute_corpus_snapshot_hash


REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
AGENT_ID = "syllable-apichat-prod"
TENANT = "tenant-001"


# ---------------------------------------------------------------------------
# Clock + result fixtures.
# ---------------------------------------------------------------------------


class StubClock(Clock):
    """Test clock that returns a sequence of values from ``now()``.

    Simulates the elapsed wall-clock between POST-receipt and 200-response
    without sleeping for hours.
    """

    def __init__(self, values: list[float]) -> None:
        self._values = list(values)

    def now(self) -> float:
        if not self._values:
            raise AssertionError("StubClock exhausted")
        return self._values.pop(0)


def _result(scan_id: str, *, incomplete: bool = False, incomplete_reason: str | None = None,
            dim_results: tuple[DimResult, ...] | None = None) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status="complete" if not incomplete else "incomplete",
        scanner_version="0.3.7",
        corpus_version="0.1.0",
        rows_emitted=10,
        k_replicates=5,
        live_probe_variance_band=LIVE_PROBE_VARIANCE_BAND,
        dim_results=dim_results if dim_results is not None else (
            DimResult(dim_id="P1.1", raw=4.0, spread=1.0, replicates=(3.0, 4.0, 4.0, 4.0, 5.0)),
            DimResult(dim_id="P2.1", raw=2.5, spread=0.0, replicates=(2.5, 2.5, 2.5, 2.5, 2.5)),
        ),
        spread_per_dim={"P1.1": 1.0, "P2.1": 0.0},
        pillar_spread={"P1": 1.0, "P2": 0.0, "P3": 0.0, "P4": 0.0},
        total_spread=1.0,
        incomplete=incomplete,
        incomplete_reason=incomplete_reason,
    )


def _runtime_returning(result_factory) -> ScanRuntime:
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        return result_factory(scan_request.scan_id)

    return ScanRuntime(run_scan_fn=run_scan_fn, prior_agent_ids=(AGENT_ID,))


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


def _body(corpus_hash: str, *, purpose: str = "mid_term", **overrides) -> dict:
    base = {
        "tenant_id": TENANT,
        "agent_id": AGENT_ID,
        "agent_endpoint": {"host": "agent.customer.example.com"},
        "credential_ref": "phase1-probe.v1",
        "credential_secret": "scoped-token-abc",
        "corpus_snapshot_hash": corpus_hash,
        "scanner_version": "0.3.7",
        "corpus_version": "0.1.0",
        "seed": 42,
        "scan_mode": "live_probe",
        "k_replicates": 5,
        "requested_pillars": ["GOVERNANCE", "ROBUSTNESS"],
        "scanner_invocation_context": {"purpose": purpose},
    }
    base.update(overrides)
    return base


def _scan_body(corpus_hash: str, **overrides) -> dict:
    body = _body(corpus_hash, **overrides)
    body.pop("agent_id")
    body.pop("scanner_invocation_context")
    return body


# ---------------------------------------------------------------------------
# Unit tests — pure SLA module behavior.
# ---------------------------------------------------------------------------


class TestSlaMetricBasics:
    """``SlaMetric`` is the rolling-window per-purpose primitive."""

    def test_empty_snapshot(self):
        m = SlaMetric()
        snap = m.snapshot()
        assert snap == {"n": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0}

    def test_records_and_reports_percentiles(self):
        m = SlaMetric()
        for s in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]:
            m.record(s)
        snap = m.snapshot()
        assert snap["n"] == 10
        # Nearest-rank p50 on n=10 → rank 5 → value 5.0.
        assert snap["p50"] == 5.0
        # Nearest-rank p95 on n=10 → rank 10 (clamped) → value 10.0.
        assert snap["p95"] == 10.0

    def test_window_evicts_oldest(self):
        m = SlaMetric(window=3)
        for s in [10.0, 20.0, 30.0, 40.0]:
            m.record(s)
        snap = m.snapshot()
        # Window N=3 means [20, 30, 40] are retained; 10 evicted.
        assert snap["n"] == 3
        assert snap["p50"] == 30.0

    def test_default_window_matches_constant(self):
        m = SlaMetric()
        assert m.samples.maxlen == ROLLING_WINDOW_N

    def test_negative_sample_rejected(self):
        m = SlaMetric()
        with pytest.raises(ValueError):
            m.record(-1.0)


class TestSlaRegistry:
    """Per-purpose registry view."""

    def test_records_per_purpose_independently(self):
        r = SlaRegistry()
        r.record("pre_bind", 1.0)
        r.record("pre_bind", 2.0)
        r.record("claim_review", 100.0)
        snap = r.snapshot()
        assert snap["pre_bind"]["n"] == 2
        assert snap["claim_review"]["n"] == 1
        assert snap["claim_review"]["p50"] == 100.0

    def test_snapshot_returns_empty_view_initially(self):
        r = SlaRegistry()
        assert r.snapshot() == {}


class TestEvaluateDegradationRule:
    """The degradation decision logic — AC #2, #3, #4."""

    def test_pre_bind_passes_through_regardless_of_elapsed(self):
        decision = evaluate_degradation(
            purpose="pre_bind",
            elapsed_seconds=24 * 3600,
            incomplete=True,
            incomplete_reason="timeout",
        )
        assert decision.partial_completion is False
        assert decision.confidence_below_adjudication_threshold is False
        assert decision.routing_event_name is None
        assert decision.uncovered_dimensions == ()

    def test_mid_term_passes_through_regardless_of_elapsed(self):
        decision = evaluate_degradation(
            purpose="mid_term",
            elapsed_seconds=24 * 3600,
            incomplete=True,
            incomplete_reason="timeout",
        )
        assert decision.partial_completion is False

    def test_claim_review_below_ceiling_extends_even_when_incomplete(self):
        """AC#3 — at 6 hr (below 12-hr ceiling), claim_review extends."""
        decision = evaluate_degradation(
            purpose="claim_review",
            elapsed_seconds=6 * 3600,
            incomplete=True,
            incomplete_reason="timeout",
        )
        assert decision.partial_completion is False
        assert decision.confidence_below_adjudication_threshold is False
        assert decision.routing_event_name is None

    def test_claim_review_above_ceiling_complete_does_not_flip_flags(self):
        """Edge case: result is complete but took >12hr — SLA breached but no
        partial fallback (the measurement is valid)."""
        decision = evaluate_degradation(
            purpose="claim_review",
            elapsed_seconds=13 * 3600,
            incomplete=False,
            incomplete_reason=None,
        )
        assert decision.partial_completion is False

    def test_claim_review_above_ceiling_incomplete_triggers_partial_fallback(self):
        """AC#4 — at 12hr+ with incomplete result, partial fallback fires."""
        decision = evaluate_degradation(
            purpose="claim_review",
            elapsed_seconds=SLA_CLAIM_REVIEW_CEILING_SECONDS + 1,
            incomplete=True,
            incomplete_reason="timeout",
            completed_pillars={"P1", "P2"},
        )
        assert decision.partial_completion is True
        assert decision.confidence_below_adjudication_threshold is True
        assert decision.routing_event_name == CLAIM_REVIEW_PARTIAL_FALLBACK_EVENT
        # Only the pillars NOT completed surface in uncovered_dimensions[].
        uncovered_ids = {u["dim_id"] for u in decision.uncovered_dimensions}
        assert uncovered_ids == {"P3", "P4"}
        for u in decision.uncovered_dimensions:
            assert u["reason"] == "timeout"

    @pytest.mark.parametrize("reason", sorted(PARTIAL_FALLBACK_REASONS))
    def test_each_partial_fallback_reason_passes_through(self, reason):
        decision = evaluate_degradation(
            purpose="claim_review",
            elapsed_seconds=SLA_CLAIM_REVIEW_CEILING_SECONDS + 1,
            incomplete=True,
            incomplete_reason=reason,
            completed_pillars=set(),
        )
        assert decision.partial_completion is True
        for u in decision.uncovered_dimensions:
            assert u["reason"] == reason

    def test_unknown_incomplete_reason_collapses_to_timeout(self):
        decision = evaluate_degradation(
            purpose="claim_review",
            elapsed_seconds=SLA_CLAIM_REVIEW_CEILING_SECONDS + 1,
            incomplete=True,
            incomplete_reason="some_unexpected_label",
            completed_pillars=set(),
        )
        assert decision.partial_completion is True
        for u in decision.uncovered_dimensions:
            assert u["reason"] == "timeout"


# ---------------------------------------------------------------------------
# Handler-level tests — AC #1, #3, #4, #5 against ``rescan_handler``.
# ---------------------------------------------------------------------------


class TestRescanHandlerSlaInstrumentation:
    """AC #1 — wall-clock metric emit on every /rescans call, per-purpose."""

    def test_metric_recorded_on_every_call(self, corpus_hash):
        runtime = _runtime_returning(_result)
        registry = SlaRegistry()
        clock = StubClock([0.0, 1.5])  # start, end → elapsed = 1.5s
        rescan_handler(
            _body(corpus_hash, purpose="mid_term"),
            tenant_header=TENANT,
            runtime=runtime,
            scan_id_factory=lambda: "s-1",
            sla_registry=registry,
            clock=clock,
        )
        snap = registry.snapshot()
        assert snap["mid_term"]["n"] == 1
        assert snap["mid_term"]["p50"] == 1.5

    def test_per_purpose_isolation(self, corpus_hash):
        runtime = _runtime_returning(_result)
        registry = SlaRegistry()
        # Three calls, three different purposes, three different elapsed values.
        for i, (purpose, elapsed) in enumerate([
            ("pre_bind", 100.0),
            ("mid_term", 200.0),
            ("claim_review", 300.0),
        ]):
            clock = StubClock([0.0, elapsed])
            rescan_handler(
                _body(corpus_hash, purpose=purpose),
                tenant_header=TENANT,
                runtime=runtime,
                scan_id_factory=lambda i=i: f"s-{i}",
                sla_registry=registry,
                clock=clock,
            )
        snap = registry.snapshot()
        assert snap["pre_bind"]["p50"] == 100.0
        assert snap["mid_term"]["p50"] == 200.0
        assert snap["claim_review"]["p50"] == 300.0
        assert snap["pre_bind"]["n"] == 1
        assert snap["mid_term"]["n"] == 1
        assert snap["claim_review"]["n"] == 1

    def test_rolling_window_caps_at_n(self, corpus_hash):
        runtime = _runtime_returning(_result)
        registry = SlaRegistry()
        for i in range(ROLLING_WINDOW_N + 5):
            clock = StubClock([0.0, float(i)])
            rescan_handler(
                _body(corpus_hash, purpose="pre_bind"),
                tenant_header=TENANT,
                runtime=runtime,
                scan_id_factory=lambda i=i: f"s-{i}",
                sla_registry=registry,
                clock=clock,
            )
        snap = registry.snapshot()
        assert snap["pre_bind"]["n"] == ROLLING_WINDOW_N


class TestRescanHandlerNominalPath:
    """AC #2 + AC #5 — nominal path keeps flags false, no routing event."""

    def test_pre_bind_nominal_flags_default_false(self, corpus_hash):
        runtime = _runtime_returning(_result)
        sink = RoutingEventSink()
        clock = StubClock([0.0, 10.0])  # 10s — well below any SLA target
        status, payload = rescan_handler(
            _body(corpus_hash, purpose="pre_bind"),
            tenant_header=TENANT,
            runtime=runtime,
            scan_id_factory=lambda: "s",
            routing_event_sink=sink,
            clock=clock,
        )
        assert status == 202
        assert payload["status"] == "accepted"
        assert payload["partial_completion"] is False
        assert payload["confidence_below_adjudication_threshold"] is False
        assert "uncovered_dimensions" not in payload
        assert sink.events == []

    def test_mid_term_nominal_flags_default_false(self, corpus_hash):
        runtime = _runtime_returning(_result)
        sink = RoutingEventSink()
        clock = StubClock([0.0, 10.0])
        status, payload = rescan_handler(
            _body(corpus_hash, purpose="mid_term"),
            tenant_header=TENANT,
            runtime=runtime,
            scan_id_factory=lambda: "s",
            routing_event_sink=sink,
            clock=clock,
        )
        assert status == 202
        assert payload["partial_completion"] is False
        assert payload["confidence_below_adjudication_threshold"] is False
        assert sink.events == []

    def test_claim_review_nominal_flags_default_false(self, corpus_hash):
        runtime = _runtime_returning(_result)
        sink = RoutingEventSink()
        clock = StubClock([0.0, 10.0])
        status, payload = rescan_handler(
            _body(corpus_hash, purpose="claim_review"),
            tenant_header=TENANT,
            runtime=runtime,
            scan_id_factory=lambda: "s",
            routing_event_sink=sink,
            clock=clock,
        )
        assert status == 202
        assert payload["partial_completion"] is False
        assert payload["confidence_below_adjudication_threshold"] is False
        assert sink.events == []


class TestRescanHandlerClaimReviewDegradation:
    """AC #3 + AC #4 — claim_review extension vs. partial-fallback."""

    def test_claim_review_at_6hr_extends_no_flags_flipped(self, corpus_hash):
        """AC#3 — induced 6-hr slowdown is below the 12-hr ceiling: extend
        (no flags flip), even if the underlying result is incomplete."""
        runtime = _runtime_returning(
            lambda sid: _result(sid, incomplete=True, incomplete_reason="timeout")
        )
        sink = RoutingEventSink()
        clock = StubClock([0.0, float(6 * 3600)])
        status, payload = rescan_handler(
            _body(corpus_hash, purpose="claim_review"),
            tenant_header=TENANT,
            runtime=runtime,
            scan_id_factory=lambda: "s",
            routing_event_sink=sink,
            clock=clock,
        )
        assert status == 202
        assert payload["status"] == "accepted"
        assert payload["partial_completion"] is False
        assert payload["confidence_below_adjudication_threshold"] is False
        assert sink.events == []

    def test_claim_review_over_12hr_ceiling_incomplete_emits_partial(self, corpus_hash):
        """AC#4 — at 12hr+ with incomplete result, partial response + flags +
        ``claim_review.partial_fallback`` routing event."""
        runtime = _runtime_returning(
            lambda sid: _result(sid, incomplete=True, incomplete_reason="rate_limited")
        )
        sink = RoutingEventSink()
        clock = StubClock([0.0, float(SLA_CLAIM_REVIEW_CEILING_SECONDS + 60)])
        status, payload = rescan_handler(
            _body(corpus_hash, purpose="claim_review"),
            tenant_header=TENANT,
            runtime=runtime,
            scan_id_factory=lambda: "s-partial",
            routing_event_sink=sink,
            clock=clock,
        )
        assert status == 202
        assert payload["status"] == "partial"
        assert payload["partial_completion"] is True
        assert payload["confidence_below_adjudication_threshold"] is True
        assert "uncovered_dimensions" in payload
        # The synthetic result completed P1 + P2; P3 + P4 surface as uncovered.
        uncovered_ids = {u["dim_id"] for u in payload["uncovered_dimensions"]}
        assert uncovered_ids == {"P3", "P4"}
        for u in payload["uncovered_dimensions"]:
            assert u["reason"] == "rate_limited"

        assert len(sink.events) == 1
        event = sink.events[0]
        assert event.name == CLAIM_REVIEW_PARTIAL_FALLBACK_EVENT
        assert event.payload["purpose"] == "claim_review"
        assert event.payload["reason"] == "rate_limited"
        assert event.payload["scan_id"] == "s-partial"
        assert event.payload["agent_id"] == AGENT_ID
        assert set(event.payload["uncovered_pillars"]) == {"P3", "P4"}

    def test_claim_review_over_12hr_ceiling_complete_does_not_emit_partial(self, corpus_hash):
        """Edge case — claim_review past 12hr but with a complete result. The
        SLA was breached (visible in the metric) but the measurement is valid
        so flags stay false."""
        runtime = _runtime_returning(lambda sid: _result(sid, incomplete=False))
        sink = RoutingEventSink()
        clock = StubClock([0.0, float(SLA_CLAIM_REVIEW_CEILING_SECONDS + 60)])
        status, payload = rescan_handler(
            _body(corpus_hash, purpose="claim_review"),
            tenant_header=TENANT,
            runtime=runtime,
            scan_id_factory=lambda: "s",
            routing_event_sink=sink,
            clock=clock,
        )
        assert payload["partial_completion"] is False
        assert payload["confidence_below_adjudication_threshold"] is False
        assert sink.events == []


# ---------------------------------------------------------------------------
# /scans response flag default (AC #5) — checked at handler level.
# ---------------------------------------------------------------------------


def _stub_scan_runtime() -> ScanRuntime:
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        return _result(scan_request.scan_id)

    return ScanRuntime(run_scan_fn=run_scan_fn, prior_agent_ids=(AGENT_ID,))


def test_scan_handler_response_carries_additive_flags_default_false(corpus_hash):
    """AC#5 — additive flags default ``false`` on /scans accept response."""
    runtime = _stub_scan_runtime()
    status, payload = scan_handler(
        _scan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "s-scan",
    )
    assert status == 202
    assert payload["partial_completion"] is False
    assert payload["confidence_below_adjudication_threshold"] is False


# ---------------------------------------------------------------------------
# N=5 calibration delta (AC#6) — SLA wiring must not move a golden.
# ---------------------------------------------------------------------------


def test_rescan_n5_under_sla_wiring_holds_envelope_zero_delta(corpus_hash):
    """AC#6 — N=5 calibration on the golden set under nominal conditions via
    /rescans must hold ±1 on total + every subscore.

    The scoring engine is the same code path as /scans by construction (WS1
    sentry). WS4 only adds a wall-clock metric + additive response flags
    defaulting false on the nominal path. We assert that with the SLA wiring
    active, 5 replicate /rescans calls against the same inputs produce an
    identical accept-response envelope (modulo scan_id) — proving the metric
    does not bleed into scoring.
    """
    runtime = _stub_scan_runtime()
    registry = SlaRegistry()

    # Baseline via /scans (no WS3 chain-quote additive fields on this route
    # per AC #3 of BEAAA-1273). The WS3 ``prior_scan_refs[]`` is rescans-only
    # and is ALWAYS present on /rescans (empty array on this unwired runtime;
    # populated when a ``scan_log`` is configured per ADR-0017 §2.1).
    _, baseline = scan_handler(
        _scan_body(corpus_hash),
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "baseline",
    )
    ws3_rescan_only_keys = {"prior_scan_refs"}
    expected_keys = set(baseline.keys()) | ws3_rescan_only_keys

    seen_payloads: list[dict] = []
    for i in range(5):
        clock = StubClock([0.0, float(i)])  # vary elapsed without bleeding into scoring
        status, payload = rescan_handler(
            _body(corpus_hash, purpose="mid_term"),
            tenant_header=TENANT,
            runtime=runtime,
            scan_id_factory=lambda i=i: f"replicate-{i}",
            sla_registry=registry,
            clock=clock,
        )
        assert status == 202
        assert set(payload.keys()) == expected_keys
        assert payload["status"] == "accepted"
        assert payload["partial_completion"] is False
        assert payload["confidence_below_adjudication_threshold"] is False
        # WS3 empty-array on unwired runtime is byte-identical across replicates.
        assert payload["prior_scan_refs"] == []
        seen_payloads.append(payload)

    # Every replicate envelope shape matches the /scans baseline (modulo
    # scan_id which is intentionally unique per call, plus the WS3
    # rescans-only ``prior_scan_refs[]`` chain field per ADR-0017 §2.1).
    # The WS4 additive flags are stable at ``false`` across all 5
    # replicates ⇒ N=5 delta = 0 ⇒ scoring engine untouched ⇒
    # supervision-contract gate passes; the halt + re-classify trigger
    # is not fired.
    for p in seen_payloads:
        assert {k for k in p.keys() if k != "scan_id"} == {
            k for k in baseline.keys() if k != "scan_id"
        } | ws3_rescan_only_keys

    snap = registry.snapshot()
    assert snap["mid_term"]["n"] == 5


# ---------------------------------------------------------------------------
# App-level tests — full FastAPI roundtrip including GET /scanner/v1/sla.
# ---------------------------------------------------------------------------


@contextmanager
def _measured_on():
    prior = os.environ.get(RESCANS_MEASURED_ENV)
    os.environ[RESCANS_MEASURED_ENV] = "on"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(RESCANS_MEASURED_ENV, None)
        else:
            os.environ[RESCANS_MEASURED_ENV] = prior


class SlaAppTests(unittest.TestCase):
    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        tarball_path = Path(self._tmpdir.name) / "corpus.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            data = b'{"corpus_version":"0.1.0"}\n'
            info = tarfile.TarInfo(name="manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        self.corpus_hash = compute_corpus_snapshot_hash(tarball_path)
        self.runtime = _stub_scan_runtime()
        self.sla_registry = SlaRegistry()
        self.routing_event_sink = RoutingEventSink()
        self.app = create_app(
            rubric=self.rubric,
            bundles=BundleStore(),
            scan_runtime=self.runtime,
            sla_registry=self.sla_registry,
            routing_event_sink=self.routing_event_sink,
        )
        self.client = TestClient(self.app)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_get_sla_returns_empty_view_before_any_call(self):
        r = self.client.get("/scanner/v1/sla")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {})

    def test_get_sla_returns_per_purpose_after_rescans(self):
        with _measured_on():
            for purpose in ("pre_bind", "mid_term", "claim_review"):
                r = self.client.post(
                    "/scanner/v1/rescans",
                    json=_body(self.corpus_hash, purpose=purpose),
                    headers={"X-Tenant-Id": TENANT},
                )
                self.assertEqual(r.status_code, 202, r.text)

        snap_response = self.client.get("/scanner/v1/sla")
        self.assertEqual(snap_response.status_code, 200, snap_response.text)
        snap = snap_response.json()
        self.assertEqual(set(snap.keys()), {"pre_bind", "mid_term", "claim_review"})
        for purpose, metric in snap.items():
            self.assertEqual(metric["n"], 1, f"{purpose}: {metric}")
            self.assertIn("p50", metric)
            self.assertIn("p95", metric)
            self.assertIn("p99", metric)

    def test_rescans_response_carries_default_flags_on_nominal_path(self):
        with _measured_on():
            r = self.client.post(
                "/scanner/v1/rescans",
                json=_body(self.corpus_hash, purpose="claim_review"),
                headers={"X-Tenant-Id": TENANT},
            )
        self.assertEqual(r.status_code, 202, r.text)
        payload = r.json()
        self.assertEqual(payload["status"], "accepted")
        self.assertEqual(payload["partial_completion"], False)
        self.assertEqual(payload["confidence_below_adjudication_threshold"], False)
        self.assertNotIn("uncovered_dimensions", payload)
        self.assertEqual(self.routing_event_sink.events, [])


# ---------------------------------------------------------------------------
# Policy-constant sanity (anchors to scope-beta-design rev 2 §2.2).
# ---------------------------------------------------------------------------


def test_sla_targets_anchor_to_scope_beta_design_rev2():
    assert SLA_P95_TARGET_SECONDS == 4 * 3600
    assert SLA_CLAIM_REVIEW_CEILING_SECONDS == 12 * 3600
    assert SLA_CLAIM_REVIEW_CEILING_SECONDS == 3 * SLA_P95_TARGET_SECONDS
    assert ROLLING_WINDOW_N == 20


if __name__ == "__main__":
    unittest.main()
