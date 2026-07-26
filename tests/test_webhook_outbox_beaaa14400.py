"""BEAAA-14400 (P1#5 fix 2) — signed terminal webhook via a durable outbox.

Parent BEAAA-14394. Covers the two halves of the fix:

* :class:`api.webhook_outbox.WebhookOutbox` — signs exactly once, de-dupes on
  the idempotency key, keeps a stable ``X-BackBond-Delivery`` across retries,
  fails closed on an unresolvable secret, retries transport failures, and parks
  a record ``dead`` after the attempt ceiling. ``deliver_pending`` is the
  retry / restart-recovery drain (composition point for fix 4, BEAAA-14401).
* :func:`api.scan_handlers.scan_handler` — emits **no** webhook at queue time,
  delivers the **terminal** customer score envelope exactly once after
  persistence, never lets a delivery failure falsify the 202, and does not fire
  for a non-terminal (retry-later) result.

Rule-execution per ``contracts/scanner-implementation-supervision-v1.md@v1.0``
(delivery plumbing on the ratified ADR-0042 §2 HMAC webhook; no scoring / rubric
/ judge / output-shape change). Contract anchor: are-scanner-api-v1@v1.0.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from api.ci_scan_ext import (
    DELIVERY_HEADER,
    SIGNATURE_HEADER,
    canonical_json,
    verify_signature,
)
from api.full_scan_executor import WorkerAttemptResult
from api.scan_handlers import ScanRuntime, scan_handler
from api.webhook_outbox import (
    STATUS_DEAD,
    STATUS_DELIVERED,
    STATUS_PENDING,
    InMemoryOutboxStore,
    WebhookOutbox,
    env_webhook_secret_resolver,
    httpx_http_poster,
    stable_delivery_id,
)
from scanners.live_probe_orchestrator import (
    LIVE_PROBE_VARIANCE_BAND,
    DimResult,
    ScanRequest,
    ScanResult,
)

_SECRET = "vendor-shared-secret"
_CORPUS_HASH = "sha256:" + "a" * 64


class _RecordingPoster:
    """Capture every delivery; optionally fail the first ``fail_first`` calls."""

    def __init__(self, *, fail_first: int = 0, fail_all: bool = False) -> None:
        self.calls: list[tuple[str, dict, bytes]] = []
        self._fail_first = fail_first
        self._fail_all = fail_all

    def __call__(self, url, headers, body_bytes) -> None:
        self.calls.append((url, dict(headers), bytes(body_bytes)))
        if self._fail_all or len(self.calls) <= self._fail_first:
            raise RuntimeError("simulated transport failure")


def _resolver(_ref: str) -> str:
    return _SECRET


def _outbox(poster, resolver=_resolver, **kw) -> WebhookOutbox:
    return WebhookOutbox(http_poster=poster, secret_resolver=resolver, **kw)


# ---------------------------------------------------------------------------
# Outbox unit behavior.
# ---------------------------------------------------------------------------


def test_delivers_signed_payload_exactly_once() -> None:
    poster = _RecordingPoster()
    outbox = _outbox(poster)
    payload = {"scan_id": "s1", "are_total": 88, "band": "B"}

    record = outbox.enqueue(
        idempotency_key="s1", url="https://ci.example/hook",
        secret_ref="vendor-x", payload=payload,
    )

    assert len(poster.calls) == 1
    url, headers, body = poster.calls[0]
    assert url == "https://ci.example/hook"
    assert body == canonical_json(payload)
    # Signature verifies with the shared secret over the exact delivered body.
    assert verify_signature(_SECRET, payload, headers[SIGNATURE_HEADER])
    assert headers[DELIVERY_HEADER] == stable_delivery_id("s1")
    assert record.status == STATUS_DELIVERED


def test_enqueue_is_idempotent_no_duplicate_delivery() -> None:
    poster = _RecordingPoster()
    outbox = _outbox(poster)
    payload = {"scan_id": "s1", "are_total": 88}

    outbox.enqueue(idempotency_key="s1", url="u", secret_ref="r", payload=payload)
    again = outbox.enqueue(idempotency_key="s1", url="u", secret_ref="r", payload=payload)

    assert len(poster.calls) == 1  # delivered once, never re-sent
    assert again.status == STATUS_DELIVERED


def test_retry_reuses_stable_delivery_id_no_duplicate() -> None:
    poster = _RecordingPoster(fail_first=1)
    outbox = _outbox(poster)
    payload = {"scan_id": "s1"}

    rec = outbox.enqueue(idempotency_key="s1", url="u", secret_ref="r", payload=payload)
    assert rec.status == STATUS_PENDING  # first attempt failed
    assert len(poster.calls) == 1

    outbox.deliver_pending()  # retry tick / restart-recovery drain

    assert len(poster.calls) == 2
    # Both the failed attempt and the retry carry the SAME delivery id, so the
    # vendor de-dupes an at-least-once delivery down to exactly-once.
    assert poster.calls[0][1][DELIVERY_HEADER] == poster.calls[1][1][DELIVERY_HEADER]
    assert outbox.get("s1").status == STATUS_DELIVERED


def test_fail_closed_on_unresolvable_secret_sends_nothing() -> None:
    poster = _RecordingPoster()
    outbox = _outbox(poster, resolver=lambda _ref: None)

    rec = outbox.enqueue(idempotency_key="s1", url="u", secret_ref="missing", payload={"a": 1})

    assert poster.calls == []  # never post an unsigned body
    assert rec.status == STATUS_PENDING
    assert "secret_resolution" in (rec.last_error or "")


def test_transport_failures_exhaust_to_dead() -> None:
    poster = _RecordingPoster(fail_all=True)
    outbox = _outbox(poster, max_attempts=3)

    outbox.enqueue(idempotency_key="s1", url="u", secret_ref="r", payload={"a": 1})
    outbox.deliver_pending()
    outbox.deliver_pending()

    rec = outbox.get("s1")
    assert rec.attempts == 3
    assert rec.status == STATUS_DEAD
    # A dead record is not retried further.
    outbox.deliver_pending()
    assert rec.attempts == 3


def test_enqueue_never_raises_on_delivery_failure() -> None:
    poster = _RecordingPoster(fail_all=True)
    outbox = _outbox(poster)
    # Must not raise even though every attempt errors.
    rec = outbox.enqueue(idempotency_key="s1", url="u", secret_ref="r", payload={"a": 1})
    assert rec.status == STATUS_PENDING


def test_env_resolver_fail_closed_and_hit() -> None:
    env = {"ARE_WEBHOOK_SECRETS_JSON": '{"vendor-x": "sekret"}'}
    resolver = env_webhook_secret_resolver(env)
    assert resolver("vendor-x") == "sekret"
    assert resolver("unknown") is None
    # Unparseable / missing config → fail closed (None), never an exception.
    assert env_webhook_secret_resolver({"ARE_WEBHOOK_SECRETS_JSON": "not json"})("x") is None
    assert env_webhook_secret_resolver({})("x") is None


# ---------------------------------------------------------------------------
# Handler acceptance — terminal delivery, no queue-time fire, no falsification.
# ---------------------------------------------------------------------------


def _synthetic_result(scan_id: str, *, incomplete: bool = False, reason: str | None = None) -> ScanResult:
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
        ),
        spread_per_dim={"P1.1": 1.0},
        pillar_spread={"P1": 1.0, "P2": 0.0, "P3": 0.0, "P4": 0.0},
        total_spread=1.0,
        incomplete=incomplete,
        incomplete_reason=reason,
    )


def _persisting_runtime(
    result: ScanResult, tmp_path: Path, *, outbox: WebhookOutbox | None = None
) -> ScanRuntime:
    """Runtime wired with a scan_log + score_envelope_factory so the terminal
    customer payload carries the persisted score envelope (are_total/band).

    BEAAA-14400 — the outbox is threaded onto the runtime (the terminal
    chokepoint that delivers), NOT passed to ``scan_handler``.
    """
    from api.scan_log import ScanLog

    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        del secret
        return result

    def score_factory(res: ScanResult, agent_id: str, run_timestamp: str) -> dict:
        return {
            "are_total": 88,
            "band": "B",
            "corpus_version": res.corpus_version,
            "scanner_version": res.scanner_version,
            "rubric_version": "1.0.0",
            "subscores": {},
            "evidence_refs": [],
            "prior_scan_refs": [],
        }

    return ScanRuntime(
        run_scan_fn=run_scan_fn,
        scan_log=ScanLog(tmp_path / "scan-log.sqlite3"),
        score_envelope_factory=score_factory,
        webhook_outbox=outbox,
    )


def _webhook_body(**overrides) -> dict:
    base = {
        "tenant_id": "tenant-001",
        "agent_id": "agent-xyz",
        "agent_endpoint": {"host": "agent.customer.example.com"},
        "credential_ref": "phase1-probe.v1",
        "credential_secret": "scoped-token-abc",
        "corpus_snapshot_hash": _CORPUS_HASH,
        "scanner_version": "0.3.0",
        "corpus_version": "0.1.0",
        "seed": 42,
        "scan_mode": "live_probe",
        "k_replicates": 5,
        "webhook": {"url": "https://ci.example/hook", "secret_ref": "vendor-x"},
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.vendor.example/x",
        "https://127.0.0.1/hook",
        "https://[::1]/hook",
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.5/hook",
        "https://192.168.1.10/hook",
        "https://user:pass@hooks.vendor.example/x",
    ],
)
def test_webhook_intake_rejects_unsafe_delivery_targets(
    tmp_path, monkeypatch, url
) -> None:
    from api.handlers import HandlerError

    monkeypatch.setenv("ARE_SCAN_WEBHOOK", "on")
    monkeypatch.setattr(
        "api.scan_handlers._verify_live_egress_fail_closed",
        lambda **_kwargs: None,
    )
    runtime = _persisting_runtime(_synthetic_result("scan-unsafe"), tmp_path)

    with pytest.raises(HandlerError) as exc_info:
        scan_handler(
            _webhook_body(webhook={"url": url, "secret_ref": "vendor-x"}),
            tenant_header="tenant-001",
            runtime=runtime,
            scan_id_factory=lambda: "scan-unsafe",
        )

    assert exc_info.value.status == 422
    assert exc_info.value.message == "webhook_url_forbidden"


def test_webhook_intake_accepts_https_public_hostname_and_stores_url(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("ARE_SCAN_WEBHOOK", "on")
    monkeypatch.setattr(
        "api.scan_handlers._verify_live_egress_fail_closed",
        lambda **_kwargs: None,
    )
    runtime = _persisting_runtime(_synthetic_result("scan-safe"), tmp_path)
    url = "https://hooks.vendor.example/x"

    status, _ = scan_handler(
        _webhook_body(webhook={"url": url, "secret_ref": "vendor-x"}),
        tenant_header="tenant-001",
        runtime=runtime,
        scan_id_factory=lambda: "scan-safe",
    )

    assert status == 202
    assert runtime._webhook_cfgs["scan-safe"]["url"] == url


def test_httpx_poster_rejects_redirect_without_following(monkeypatch) -> None:
    calls = []

    class _RedirectResponse:
        is_redirect = True

        def raise_for_status(self) -> None:
            return None

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _RedirectResponse()

    monkeypatch.setattr("httpx.post", fake_post)

    with pytest.raises(RuntimeError, match="redirect"):
        httpx_http_poster("https://hooks.vendor.example/x", {}, b"{}")

    assert calls[0][1]["follow_redirects"] is False


def test_terminal_webhook_delivered_not_queue_time_payload(tmp_path) -> None:
    poster = _RecordingPoster()
    outbox = _outbox(poster)
    runtime = _persisting_runtime(_synthetic_result("scan-t1"), tmp_path, outbox=outbox)

    status, response = scan_handler(
        _webhook_body(),
        tenant_header="tenant-001",
        runtime=runtime,
        scan_id_factory=lambda: "scan-t1",
    )

    assert status == 202
    # The 202 accept envelope is the thin queue-time shape — no score fields.
    assert "are_total" not in response
    assert response["status"] == "accepted"

    # Exactly one webhook, and it carries the TERMINAL customer score envelope
    # (persisted are_total/band), proving it fired after persistence, not at
    # queue time with the 202 body.
    assert len(poster.calls) == 1
    _url, headers, body = poster.calls[0]
    import json

    delivered = json.loads(body)
    assert delivered["scan_id"] == "scan-t1"
    assert delivered["are_total"] == 88
    assert delivered["band"] == "B"
    assert verify_signature(_SECRET, delivered, headers[SIGNATURE_HEADER])


def test_delivery_failure_does_not_falsify_scan_status(tmp_path) -> None:
    poster = _RecordingPoster(fail_all=True)
    outbox = _outbox(poster)
    runtime = _persisting_runtime(_synthetic_result("scan-t2"), tmp_path, outbox=outbox)

    status, response = scan_handler(
        _webhook_body(),
        tenant_header="tenant-001",
        runtime=runtime,
        scan_id_factory=lambda: "scan-t2",
    )

    # The scan still succeeds despite the delivery error.
    assert status == 202
    assert response["scan_id"] == "scan-t2"
    assert response["status"] == "accepted"
    assert outbox.get("scan-t2").status == STATUS_PENDING  # queued for retry


def test_no_webhook_when_scan_not_terminal(tmp_path) -> None:
    poster = _RecordingPoster()
    outbox = _outbox(poster)
    runtime = _persisting_runtime(
        _synthetic_result("scan-t3", incomplete=True, reason="full_scan_registry_lease_busy"),
        tmp_path,
        outbox=outbox,
    )

    status, _response = scan_handler(
        _webhook_body(),
        tenant_header="tenant-001",
        runtime=runtime,
        scan_id_factory=lambda: "scan-t3",
    )

    assert status == 202
    assert poster.calls == []  # retry-later result must not emit a webhook
    assert outbox.get("scan-t3") is None
    # The config stays registered so the eventual terminal record can deliver.
    assert runtime._webhook_cfgs.get("scan-t3") == {
        "url": "https://ci.example/hook",
        "secret_ref": "vendor-x",
    }


def test_no_outbox_means_no_delivery(tmp_path) -> None:
    # Legacy byte-identical path: no outbox wired ⇒ no delivery, no error.
    runtime = _persisting_runtime(_synthetic_result("scan-t4"), tmp_path)
    status, response = scan_handler(
        _webhook_body(),
        tenant_header="tenant-001",
        runtime=runtime,
        scan_id_factory=lambda: "scan-t4",
    )
    assert status == 202
    assert response["scan_id"] == "scan-t4"


# ---------------------------------------------------------------------------
# Async full-scan path — the CTO REQUEST-CHANGES coverage gap: the earlier
# tests stub a *sync* run_scan_fn, so the queued-202 → terminal transition was
# never exercised. This drives the real daemon-worker branch.
# ---------------------------------------------------------------------------

_ASYNC_REGISTRY_ROWS = [
    {
        "test_id": "promptinject",
        "case_id": "promptinject.case_001",
        "status": "active",
        "production_runner_executable": True,
        "risk_or_rubric_id": "P3.1",
        "runner": "promptfoo_wrapper",
    },
]


def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _async_full_scan_runtime(outbox, worker):
    """Runtime whose full-scan tier runs on the async daemon-worker branch."""
    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        # The async branch never calls this — full scans dispatch the registry
        # on a background thread. A stub keeps the contract honest.
        del secret
        raise AssertionError("sync run_scan_fn must not run for an async full scan")

    return ScanRuntime(
        run_scan_fn=run_scan_fn,
        full_scan_registry_rows=_ASYNC_REGISTRY_ROWS,
        full_scan_workers_factory=lambda _req, _secret: {"promptfoo": worker},
        webhook_outbox=outbox,
    )


def test_async_full_scan_emits_terminal_webhook_not_at_queue_time(tmp_path, monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-14400-test-key")
    monkeypatch.setattr(
        "api.scan_handlers._verify_live_egress_fail_closed",
        lambda **_kwargs: None,
    )
    poster = _RecordingPoster()
    outbox = _outbox(poster)

    release = threading.Event()
    started = threading.Event()

    def worker(row, job):
        started.set()
        # Hold the background worker mid-flight so we can prove nothing is
        # delivered at queue time (the 202 has already returned by now).
        assert release.wait(timeout=5), "worker gate never released"
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=f"evidence://{job.job_id}/{row['case_id']}.json",
            model_version="fixture-agent-v0",
        )

    runtime = _async_full_scan_runtime(outbox, worker)

    status, response = scan_handler(
        _webhook_body(),
        tenant_header="tenant-001",
        runtime=runtime,
        scan_id_factory=lambda: "scan-async",
    )

    # Queue time: 202 queued, the background worker is blocked, and NO webhook
    # has fired (the original defect fired here with the 202 payload).
    assert status == 202
    assert response["scan_id"] == "scan-async"
    assert _wait_until(started.is_set)
    assert runtime.fetch("scan-async").status in {"queued", "running"}
    assert poster.calls == []

    # Let the worker finish → terminal persistence → exactly one webhook.
    release.set()
    assert _wait_until(lambda: runtime.fetch("scan-async").status == "complete")
    assert _wait_until(lambda: len(poster.calls) == 1)

    _url, headers, body = poster.calls[0]
    delivered = json.loads(body)
    assert delivered["scan_id"] == "scan-async"
    assert delivered["status"] == "complete"
    assert verify_signature(_SECRET, delivered, headers[SIGNATURE_HEADER])
    assert outbox.get("scan-async").status == STATUS_DELIVERED
    # The registration is consumed on terminal delivery — no re-emit surface.
    assert runtime._webhook_cfgs.get("scan-async") is None

    # Retry / restart-recovery drain must NOT duplicate the delivery.
    outbox.deliver_pending()
    assert len(poster.calls) == 1
    delivery_id = headers[DELIVERY_HEADER]
    assert delivery_id == stable_delivery_id("scan-async")


def test_scan_reached_terminal_excludes_queued_and_running() -> None:
    # CTO review req 2 — queued/running placeholders carry incomplete=False but
    # are NOT terminal, so the status guard must reject them.
    from api.scan_handlers import _scan_reached_terminal

    for status in ("queued", "running"):
        placeholder = ScanResult(
            scan_id="s", status=status, scanner_version="0.3.0",
            corpus_version="0.1.0", rows_emitted=0, k_replicates=5,
        )
        assert _scan_reached_terminal(placeholder) is False

    # A completed scan and a terminally-incomplete "REFER" ARE terminal.
    assert _scan_reached_terminal(_synthetic_result("s")) is True
    assert _scan_reached_terminal(
        _synthetic_result("s", incomplete=True, reason="variance_band_exceeded_total")
    ) is True
    # A retry-later lease-busy result is NOT terminal.
    assert _scan_reached_terminal(
        _synthetic_result("s", incomplete=True, reason="full_scan_registry_lease_busy")
    ) is False
