"""SLA instrumentation + degradation policy for POST /scanner/v1/rescans.

BEAAA-1274 (WS4 of BEAAA-1255) — rule-execution per
``contracts/scanner-implementation-supervision-v1.md@v1.0`` classification table
row "Refactor that doesn't touch behavior" (no scoring math, no judge-model, no
rubric touch; additive wall-clock metric + additive response-flag plumbing).

SLA policy authority — scope-beta-design rev 2 §2.2 + HoUW Q2a/Q2b binding
co-sign (BEAAA-1243). The umbrella v1.1.0 → v1.2.0 contract bump (ADR-0009 §3.2,
WS7 / BEAAA-1272) carries the CTO co-sign for the two additive boolean flags;
WS4 wires them with default-``false`` semantics that keeps the change
wire-additive on /scans + nominal-path /rescans.

Discipline (Supreme Law / Scanner Engineer OS):

* The N=5 calibration delta on the golden set under nominal conditions via
  /rescans must hold ±1 on total + every subscore — verified by the WS1 sentry
  (``test_rescan_n5_delta_is_zero_by_construction``) plus this WS4's
  ``test_sla_nominal_path_does_not_flip_flags_or_emit_routing``.
* If the metric or degradation path ever moves a golden by >1, halt + reclassify
  as rule-change + escalate to CTO. Not triggered at WS4 (additive only).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping


# ---------------------------------------------------------------------------
# Policy constants (scope-beta-design rev 2 §2.2; HoUW Q2a/Q2b co-sign).
# ---------------------------------------------------------------------------

# 4 hr p95 target on every purpose (3.3% of Tier-2 ≤15 BD coverage-decision
# envelope per BEAAA-622 §7). Per scope-beta-design rev 2 §2.2.
SLA_P95_TARGET_SECONDS: int = 4 * 3600

# 12 hr ceiling for ``claim_review`` (10% of envelope). Below this we extend
# rather than fall back to partial; at-or-above this we trigger the partial-
# fallback + manual-routing path. Per HoUW Q2b binding co-sign (BEAAA-1243).
SLA_CLAIM_REVIEW_CEILING_SECONDS: int = 12 * 3600

# Rolling-window N for p50/p95/p99. Suggested by BEAAA-1274 scope #1; pinned
# here so the metric snapshot shape is deterministic.
ROLLING_WINDOW_N: int = 20

# Purpose-keyed routing event name (BEAAA-1274 scope #4). Emitted when both
# partial-completion flags flip true on a claim_review rescan; the Claims
# Architect's manual-review pipeline subscribes to this event.
CLAIM_REVIEW_PARTIAL_FALLBACK_EVENT: str = "claim_review.partial_fallback"

# Partial-fallback reason codes (BEAAA-1274 scope #4). Surface in the
# response's ``uncovered_dimensions[].reason`` and on the routing event payload.
PARTIAL_FALLBACK_REASONS: frozenset[str] = frozenset(
    {"timeout", "rate_limited", "transport_saturated"}
)


# ---------------------------------------------------------------------------
# Clock abstraction — injectable so tests can simulate elapsed wall-clock
# without sleeping for 12 hours. Production wiring uses ``time.monotonic``.
# ---------------------------------------------------------------------------


class Clock:
    """Monotonic-clock surface used by the SLA timer.

    Default backing is ``time.monotonic``. Tests inject a stubbed clock to
    simulate elapsed wall-clock without sleeping (e.g., the 6-hr-extends and
    >12-hr-falls-back tests in ``tests/test_sla_beaaa1274.py``).
    """

    def now(self) -> float:
        return time.monotonic()


# ---------------------------------------------------------------------------
# Per-purpose rolling-window metric — p50 / p95 / p99 over the last N samples.
# ---------------------------------------------------------------------------


@dataclass
class SlaMetric:
    """Rolling-window wall-clock samples for one ``purpose``.

    Samples are seconds (float). ``snapshot()`` returns the current p50/p95/p99
    plus sample count; this is the surface "observable via metrics surface"
    referenced by BEAAA-1274 AC #1.
    """

    window: int = ROLLING_WINDOW_N
    samples: deque[float] = field(default_factory=deque)

    def __post_init__(self) -> None:
        # ``deque(maxlen=N)`` evicts oldest on append — exactly the rolling
        # window we want. Re-bind here so the dataclass default plays nice
        # with ``deque(maxlen=...)``.
        if self.samples.maxlen != self.window:
            existing = list(self.samples)
            self.samples = deque(existing, maxlen=self.window)

    def record(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("sla sample seconds must be non-negative")
        self.samples.append(float(seconds))

    def snapshot(self) -> dict[str, float | int]:
        n = len(self.samples)
        if n == 0:
            return {"n": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0}
        ordered = sorted(self.samples)
        return {
            "n": n,
            "p50": _percentile(ordered, 50),
            "p95": _percentile(ordered, 95),
            "p99": _percentile(ordered, 99),
        }


def _percentile(ordered: list[float], pct: int) -> float:
    """Nearest-rank percentile. Cheap, deterministic, and the right call for
    a rolling window of N=20 where interpolation noise dominates signal.
    """
    if not ordered:
        return 0.0
    if pct <= 0:
        return ordered[0]
    if pct >= 100:
        return ordered[-1]
    # Nearest-rank: index = ceil(pct/100 * n) - 1, clamped to [0, n-1].
    n = len(ordered)
    rank = max(1, int((pct / 100.0) * n + 0.5))
    return ordered[min(rank - 1, n - 1)]


class SlaRegistry:
    """Per-purpose metric registry.

    Lazily creates an ``SlaMetric`` per ``purpose`` seen at record time.
    ``snapshot()`` returns the full registry view for the metrics surface.
    """

    def __init__(self, *, window: int = ROLLING_WINDOW_N) -> None:
        self._window = window
        self._metrics: dict[str, SlaMetric] = {}

    def record(self, purpose: str, seconds: float) -> None:
        metric = self._metrics.get(purpose)
        if metric is None:
            metric = SlaMetric(window=self._window)
            self._metrics[purpose] = metric
        metric.record(seconds)

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        return {purpose: m.snapshot() for purpose, m in self._metrics.items()}

    def reset(self) -> None:
        self._metrics.clear()


# ---------------------------------------------------------------------------
# Routing-event sink — log + metric path the Claims Architect's manual-review
# pipeline subscribes to. Tests inject a capturing sink; production wires to
# the standard structured logger.
# ---------------------------------------------------------------------------


@dataclass
class RoutingEvent:
    name: str
    payload: dict[str, Any]


class RoutingEventSink:
    """Pluggable sink for partial-fallback routing events.

    Default sink appends to an in-memory list and writes a single line to the
    standard logger so production wiring picks them up via the existing log
    aggregation surface. Tests inject a sink that captures events for
    assertion.
    """

    def __init__(self) -> None:
        self.events: list[RoutingEvent] = []

    def emit(self, name: str, payload: Mapping[str, Any]) -> None:
        event = RoutingEvent(name=name, payload=dict(payload))
        self.events.append(event)
        # Keep the production log line minimal + structured-key oriented; the
        # Claims Architect pipeline grep targets ``event_name=`` per
        # scope-beta-design rev 2 §2.2 manual-routing hook.
        _log_routing_event(name, payload)


def _log_routing_event(name: str, payload: Mapping[str, Any]) -> None:
    # Single point of side-effect for the log line; isolated so tests don't
    # need to capture stderr. Tests use the in-memory ``events`` list instead.
    import logging

    logger = logging.getLogger("are_scanner.sla")
    logger.info(
        "sla_routing_event",
        extra={"event_name": name, "payload": dict(payload)},
    )


# ---------------------------------------------------------------------------
# Degradation decision — the central rule.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DegradationDecision:
    """Outcome of evaluating the SLA degradation rule for one rescan call.

    Wire shape on a /rescans 202 response:

    * ``partial_completion`` and ``confidence_below_adjudication_threshold`` go
      into the response body as additive boolean fields (BEAAA-1274 #4).
    * ``uncovered_dimensions`` is populated only on the partial-fallback path;
      each entry carries ``{"dim_id", "reason"}`` per scope-beta-design rev 2
      §2.2. Default-empty on the nominal path.
    * ``routing_event_name`` is the event name to emit on the sink; ``None``
      on the nominal path. ``routing_event_payload`` carries the structured
      payload (purpose, elapsed seconds, scan id, reason). Caller is
      responsible for actually emitting via the sink.
    """

    partial_completion: bool
    confidence_below_adjudication_threshold: bool
    uncovered_dimensions: tuple[dict[str, str], ...] = ()
    routing_event_name: str | None = None
    routing_event_payload: dict[str, Any] = field(default_factory=dict)


def evaluate_degradation(
    *,
    purpose: str,
    elapsed_seconds: float,
    incomplete: bool,
    incomplete_reason: str | None,
    completed_pillars: Iterable[str] = (),
    expected_pillars: Iterable[str] = ("P1", "P2", "P3", "P4"),
) -> DegradationDecision:
    """Decide whether a /rescans call hit the SLA degradation path.

    Rules (scope-beta-design rev 2 §2.2 + HoUW Q2a/Q2b co-sign):

    * ``pre_bind`` / ``mid_term``: no degradation policy beyond the 4-hr p95
      target. Flags stay ``false``; the existing v1.1.0 ``uncovered_dimensions[]``
      semantics handle partial-pillar surface on the final score envelope.
    * ``claim_review`` and ``elapsed < 12hr ceiling``: prefer extension. Flags
      stay ``false`` even if the underlying scan is incomplete — the
      orchestrator is still allowed to continue inside the 12-hr ceiling.
    * ``claim_review`` and ``elapsed ≥ 12hr ceiling`` and the scan did not
      finish cleanly (``incomplete`` true): trigger partial fallback. Both
      flags flip ``true``; ``uncovered_dimensions[]`` is populated from the
      pillars that didn't complete; ``incomplete_reason`` (if known + valid)
      becomes the per-entry ``reason``; the ``claim_review.partial_fallback``
      routing event fires.
    """
    if purpose != "claim_review":
        return DegradationDecision(
            partial_completion=False,
            confidence_below_adjudication_threshold=False,
        )

    if elapsed_seconds < SLA_CLAIM_REVIEW_CEILING_SECONDS:
        # Extension preferred — the orchestrator keeps measuring. Flags stay
        # false regardless of the orchestrator's current incomplete state;
        # the partial-fallback gate is the 12-hr ceiling, not the 4-hr target.
        return DegradationDecision(
            partial_completion=False,
            confidence_below_adjudication_threshold=False,
        )

    # ``elapsed_seconds >= SLA_CLAIM_REVIEW_CEILING_SECONDS`` — ceiling breach.
    if not incomplete:
        # Edge case: a fully-complete result that took >12 hr. We still
        # accept the result without flipping flags — the measurement is
        # valid, it just blew the SLA. The wall-clock metric will surface
        # the breach; partial fallback is for *incomplete* results past
        # the ceiling.
        return DegradationDecision(
            partial_completion=False,
            confidence_below_adjudication_threshold=False,
        )

    reason = incomplete_reason if incomplete_reason in PARTIAL_FALLBACK_REASONS else "timeout"
    completed = set(completed_pillars)
    uncovered = tuple(
        {"dim_id": pillar, "reason": reason}
        for pillar in expected_pillars
        if pillar not in completed
    )
    return DegradationDecision(
        partial_completion=True,
        confidence_below_adjudication_threshold=True,
        uncovered_dimensions=uncovered,
        routing_event_name=CLAIM_REVIEW_PARTIAL_FALLBACK_EVENT,
        routing_event_payload={
            "purpose": purpose,
            "elapsed_seconds": float(elapsed_seconds),
            "reason": reason,
            "uncovered_pillars": [u["dim_id"] for u in uncovered],
        },
    )


def pillars_from_dim_results(dim_results: Iterable[Any]) -> set[str]:
    """Reduce a tuple of ``DimResult`` (or dicts with ``dim_id``) to the set of
    top-level pillar codes (the leading ``P<n>`` token of each dim_id).

    Used by ``rescan_handler`` to decide which pillars are "completed" when
    deriving ``uncovered_dimensions[]`` on partial fallback.
    """
    pillars: set[str] = set()
    for d in dim_results:
        dim_id = getattr(d, "dim_id", None)
        if dim_id is None and isinstance(d, dict):
            dim_id = d.get("dim_id")
        if not isinstance(dim_id, str):
            continue
        head = dim_id.split(".", 1)[0]
        if head:
            pillars.add(head)
    return pillars


__all__ = [
    "CLAIM_REVIEW_PARTIAL_FALLBACK_EVENT",
    "Clock",
    "DegradationDecision",
    "PARTIAL_FALLBACK_REASONS",
    "ROLLING_WINDOW_N",
    "RoutingEvent",
    "RoutingEventSink",
    "SLA_CLAIM_REVIEW_CEILING_SECONDS",
    "SLA_P95_TARGET_SECONDS",
    "SlaMetric",
    "SlaRegistry",
    "evaluate_degradation",
    "pillars_from_dim_results",
]
