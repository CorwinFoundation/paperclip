"""BEAAA-14399 (P1#5 fix 3) — hold the EdgeGateway concurrency lease until the
full scan is terminal.

After the async full-scan cutover (PR #71), ``ScanRuntime.kickoff`` returns a
``queued`` 202 immediately and runs the evidence-producing suite on a daemon
worker. The per-tenant admission lease must therefore be held for the *actual
execution*, not merely until the handler returns the 202 — otherwise a second
scan for the same tenant is wrongly admitted while the first is still running.

These tests fail before the fix (the lease was released in the handler's
``finally`` right after the 202) and pass after it (ownership of the terminal
release is handed to the runtime's job lifecycle).

Acceptance (from BEAAA-14394 / BEAAA-14399): with ``max_concurrent=1``, a
deliberately-blocked first full scan rejects a second request with the
documented ``429 rate_limited`` until the first becomes terminal, then admits
the next.
"""
from __future__ import annotations

import threading
import time
import unittest
import unittest.mock
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest(f"fastapi not installed: {exc}")

from api.app import create_app
from api.bundle import BundleStore
from api.edge_gateway import EdgeGateway, KeyStore, RateLimitConfig, RateLimiter
from api.full_scan_executor import WorkerAttemptResult
from api.scan_handlers import ScanRuntime
from harness import egress
from rubric.scoring import Rubric
from scanners.live_probe_orchestrator import ScanRequest

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"

TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
VALID_SHA = "sha256:" + "0" * 64


class FakeClock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _registry_row() -> dict[str, object]:
    """One active, production-executable registry row that maps to the
    ``inspect`` framework worker (mirrors the async-kickoff fixture)."""
    return {
        "test_id": "p1_1_factuality_golden",
        "case_id": "P1.1-FAC-001",
        "status": "active",
        "pillar": "OPS",
        "category": "factuality",
        "risk_or_rubric_id": "P1.1",
        "source_pack": "rubric/probe_mappings/p1_1_factuality_golden.yaml",
        "runner": "bench_wrapper",
        "version_pin": "p1_1_factuality_golden@0.1.0",
        "production_runner_executable": True,
    }


def _scan_request(scan_id: str) -> ScanRequest:
    return ScanRequest(
        scan_id=scan_id,
        tenant_id="tenant-test",
        agent_endpoint="https://agent.example.test",
        credential_ref="phase1-probe.v1",
        corpus_snapshot_hash=VALID_SHA,
        scanner_version="0.4.2",
        corpus_version="lease-test@2026-07-15",
        seed=42,
        k_replicates=5,
    )


def _blocking_workers_factory(gate: threading.Event, entered: threading.Event):
    """A full-scan workers factory whose sole worker blocks on ``gate`` — the
    scan stays ``running`` (lease held) until the test opens the gate."""

    def worker(_row, _job):
        entered.set()
        assert gate.wait(timeout=5), "worker gate never opened"
        return WorkerAttemptResult(
            status="complete",
            evidence_ref="evidence://lease-test/P1.1-FAC-001.json",
            verdict="pass",
            target_calls=1,
            judge_calls=1,
        )

    return lambda _request, _secret: {"inspect": worker}


def _drain_to_terminal(runtime: ScanRuntime, scan_id: str, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = runtime.fetch(scan_id)
        if result is not None and result.status not in {"queued", "running"}:
            return result.status
        time.sleep(0.01)
    raise AssertionError(f"scan {scan_id} did not reach a terminal state")


# ---------------------------------------------------------------------------
# Component level — kickoff fires on_terminal exactly once, at terminal.
# ---------------------------------------------------------------------------


class KickoffTerminalCallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        import os

        os.environ.setdefault("ARE_BUNDLE_SIGNING_KEY", "lease-until-terminal-test-key")

    def test_async_full_scan_fires_on_terminal_only_after_completion(self) -> None:
        gate = threading.Event()
        entered = threading.Event()
        released: list[float] = []

        runtime = ScanRuntime(
            run_scan_fn=lambda _r, _s: (_ for _ in ()).throw(
                AssertionError("legacy walker must not run for a full registry scan")
            ),
            full_scan_registry_rows=[_registry_row()],
            full_scan_workers_factory=_blocking_workers_factory(gate, entered),
        )
        request = _scan_request("scan-async-terminal")

        queued = runtime.kickoff(
            request, "secret", on_terminal=lambda: released.append(time.monotonic())
        )
        self.assertEqual(queued.status, "queued")

        # Worker is running and blocked → terminal callback must NOT have fired.
        self.assertTrue(entered.wait(timeout=2))
        self.assertEqual(released, [], "lease released while scan still running")

        gate.set()
        self.assertEqual(_drain_to_terminal(runtime, request.scan_id), "complete")
        # Give the worker thread's finally a beat to run the callback.
        for _ in range(200):
            if released:
                break
            time.sleep(0.01)
        self.assertEqual(len(released), 1, "on_terminal must fire exactly once at terminal")

    def test_async_full_scan_fires_on_terminal_even_when_worker_errors(self) -> None:
        released: list[int] = []

        def exploding_factory(_request, _secret):
            def worker(_row, _job):
                raise RuntimeError("boom")

            return {"inspect": worker}

        runtime = ScanRuntime(
            run_scan_fn=lambda _r, _s: (_ for _ in ()).throw(AssertionError("nope")),
            full_scan_registry_rows=[_registry_row()],
            full_scan_workers_factory=exploding_factory,
        )
        request = _scan_request("scan-async-error")
        runtime.kickoff(request, "secret", on_terminal=lambda: released.append(1))

        # A worker exception is still terminal (failed/incomplete), so the
        # admission lease must be released.
        _drain_to_terminal(runtime, request.scan_id)
        for _ in range(200):
            if released:
                break
            time.sleep(0.01)
        self.assertEqual(len(released), 1)

    def test_synchronous_scan_fires_on_terminal_inline(self) -> None:
        from api.scan_handlers import ScanRuntime as _SR
        from scanners.live_probe_orchestrator import ScanResult

        released: list[int] = []

        def sync_run(scan_request, _secret):
            return ScanResult(
                scan_id=scan_request.scan_id,
                status="complete",
                scanner_version="0.4.2",
                corpus_version="lease-test",
                rows_emitted=1,
                k_replicates=scan_request.k_replicates,
            )

        runtime = _SR(run_scan_fn=sync_run)
        request = _scan_request("scan-sync-terminal")
        result = runtime.kickoff(request, "secret", on_terminal=lambda: released.append(1))

        # Synchronous path: terminal before kickoff returns → released inline.
        self.assertEqual(result.status, "complete")
        self.assertEqual(len(released), 1)

    def test_worker_start_failure_releases_inline(self) -> None:
        released: list[int] = []
        runtime = ScanRuntime(
            run_scan_fn=lambda _r, _s: (_ for _ in ()).throw(AssertionError("nope")),
            full_scan_registry_rows=[_registry_row()],
            full_scan_workers_factory=lambda _request, _secret: {},
        )

        with unittest.mock.patch.object(
            threading.Thread, "start", side_effect=RuntimeError("cannot start")
        ):
            with self.assertRaisesRegex(RuntimeError, "cannot start"):
                runtime.kickoff(
                    _scan_request("scan-start-failure"),
                    "secret",
                    on_terminal=lambda: released.append(1),
                )

        self.assertEqual(released, [1])


# ---------------------------------------------------------------------------
# End-to-end — HTTP admission holds max_concurrent for actual execution.
# ---------------------------------------------------------------------------


class LeaseHeldUntilTerminalHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        import os

        os.environ.setdefault("ARE_BUNDLE_SIGNING_KEY", "lease-until-terminal-test-key")
        self.rubric = Rubric.load(RUBRIC_PATH)
        self._gate = threading.Event()
        self._entered = threading.Event()
        # Do not depend on the sandbox network posture for the egress control.
        self._egress_patches = [
            unittest.mock.patch.object(
                egress, "verify_live_egress_fail_closed", lambda **_k: {}
            ),
            unittest.mock.patch.object(egress, "is_enabled", lambda: False),
        ]
        for p in self._egress_patches:
            p.start()

    def tearDown(self) -> None:
        self._gate.set()  # never leave a worker thread blocked
        for p in self._egress_patches:
            p.stop()

    def _runtime(self) -> ScanRuntime:
        return ScanRuntime(
            run_scan_fn=lambda _r, _s: (_ for _ in ()).throw(
                AssertionError("legacy walker must not run")
            ),
            full_scan_registry_rows=[_registry_row()],
            full_scan_workers_factory=_blocking_workers_factory(self._gate, self._entered),
        )

    def _client(self, runtime: ScanRuntime):
        clock = FakeClock()
        store = KeyStore(None, clock=clock, bootstrap_env={"tok": TENANT_A})
        gateway = EdgeGateway(
            store=store,
            limiter=RateLimiter(clock=clock),
            defaults=RateLimitConfig(scans_per_min=1000, max_concurrent=1),
        )
        app = create_app(rubric=self.rubric, bundles=BundleStore(), scan_runtime=runtime)
        app.state.edge_gateway = gateway
        return TestClient(app, raise_server_exceptions=False)

    def _body(self, **overrides) -> dict:
        body = {
            "tenant_id": TENANT_A,
            "agent_endpoint": {"host": "agent.customer.example.com"},
            "credential_ref": "phase1-probe.v1",
            "credential_secret": "scoped-token-abc",
            "corpus_snapshot_hash": VALID_SHA,
            "scanner_version": "0.4.2",
            "corpus_version": "lease-test@2026-07-15",
            "seed": 42,
            "scan_mode": "live_probe",
            "k_replicates": 5,
        }
        body.update(overrides)
        return body

    def test_validation_failure_releases_for_next_request(self) -> None:
        runtime = self._runtime()
        client = self._client(runtime)
        headers = {"Authorization": "Bearer tok"}

        invalid = client.post(
            "/scanner/v1/scans",
            json=self._body(credential_secret=""),
            headers=headers,
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)

        admitted = client.post("/scanner/v1/scans", json=self._body(), headers=headers)
        self.assertEqual(admitted.status_code, 202, admitted.text)

    def test_rescan_holds_lease_until_terminal(self) -> None:
        runtime = self._runtime()
        runtime._agent_ids_with_prior_scan["agent-lease-test"] = runtime._now()
        client = self._client(runtime)
        headers = {"Authorization": "Bearer tok"}
        body = self._body(
            agent_id="agent-lease-test",
            scanner_invocation_context={"purpose": "mid_term"},
        )

        first = client.post("/scanner/v1/rescans", json=body, headers=headers)
        self.assertEqual(first.status_code, 202, first.text)
        self.assertTrue(self._entered.wait(timeout=2), "rescan worker never started")

        second = client.post("/scanner/v1/rescans", json=body, headers=headers)
        self.assertEqual(second.status_code, 429, second.text)
        self.assertEqual(second.json()["reason"], "max_concurrent")

        self._gate.set()
        self.assertEqual(_drain_to_terminal(runtime, first.json()["scan_id"]), "complete")

    def test_second_scan_429_until_first_terminal_then_admitted(self) -> None:
        runtime = self._runtime()
        client = self._client(runtime)
        headers = {"Authorization": "Bearer tok"}

        # First full scan → 202 queued; the daemon worker is now running and
        # blocked, so the admission lease is held.
        first = client.post("/scanner/v1/scans", json=self._body(), headers=headers)
        self.assertEqual(first.status_code, 202, first.text)
        scan_id = first.json()["scan_id"]
        # The scan is executing on the background worker (the HTTP accept
        # envelope reports "accepted"; the internal job is queued/running).
        self.assertTrue(self._entered.wait(timeout=2), "worker never started")
        in_flight = runtime.fetch(scan_id)
        self.assertIsNotNone(in_flight)
        self.assertIn(in_flight.status, {"queued", "running"})

        # Second scan for the same tenant while the first is still running →
        # 429 rate_limited. THIS is the assertion that fails before the fix.
        second = client.post("/scanner/v1/scans", json=self._body(), headers=headers)
        self.assertEqual(second.status_code, 429, second.text)
        self.assertEqual(second.json()["reason"], "max_concurrent")
        self.assertIn("Retry-After", second.headers)

        # Let the first scan finish → terminal → lease released.
        self._gate.set()
        self.assertEqual(_drain_to_terminal(runtime, scan_id), "complete")

        # Next scan is admitted again (202) now that the slot is free.
        third = client.post("/scanner/v1/scans", json=self._body(), headers=headers)
        self.assertEqual(third.status_code, 202, third.text)


if __name__ == "__main__":  # pragma: no cover
    import unittest.mock  # noqa: F401 — ensure mock is importable when run directly

    unittest.main()
