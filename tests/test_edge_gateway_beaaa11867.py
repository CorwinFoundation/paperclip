"""BEAAA-11867 — D4 key store + D3 per-tenant rate limit (app gateway).

Acceptance (per the parent build design BEAAA-11862):
  (a) revoked key  → 401 within cache-TTL, no restart
  (b) expired key  → 401
  (c) rotation grace-overlap: both keys valid, then old revoked
  (d) N+1th scan/min → 429 + Retry-After
  (e) M+1th concurrent → 429
  (f) two keys same source IP → independent buckets (keyed on resolved
      identity, NOT IP)
  (g) per-tenant override honored

Rate-limit acceptance is proven precisely at the component level (RateLimiter
with an injected clock) and its HTTP wiring is proven end-to-end through
``create_app``. True cross-request parallelism is not exercised via TestClient
(the sync ASGI loop serialises requests); the concurrency-limit HTTP test
pre-seeds an in-flight lease and asserts the app returns 429 — equivalent to a
concurrent M+1th arrival.
"""
from __future__ import annotations

import unittest

try:
    from fastapi.testclient import TestClient
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest(f"fastapi not installed: {exc}")

from api.app import create_app
from api.bundle import BundleStore
from api.edge_gateway import (
    EdgeGateway,
    KeyStore,
    RateLimitConfig,
    RateLimited,
    RateLimiter,
)
from api.scan_handlers import ScanRuntime
from rubric.scoring import Rubric
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"

TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class FakeClock:
    """Injectable wall-clock for deterministic TTL/expiry/refill tests."""

    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# ---------------------------------------------------------------------------
# D4 key store — component
# ---------------------------------------------------------------------------


class KeyStoreTests(unittest.TestCase):
    def _store(self, tmp: Path, clock: FakeClock, ttl: float = 5.0) -> KeyStore:
        return KeyStore(tmp / "keys.json", cache_ttl_seconds=ttl, clock=clock)

    def setUp(self) -> None:
        import tempfile

        self._tmp = Path(tempfile.mkdtemp())

    def test_active_key_resolves(self) -> None:
        clock = FakeClock()
        store = self._store(self._tmp, clock)
        store.issue(token="tok", key_id="k1", tenant_id=TENANT_A, ttl_seconds=3600)
        rec = store.resolve("tok")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.tenant_id, TENANT_A)

    def test_unknown_token_resolves_none(self) -> None:
        clock = FakeClock()
        store = self._store(self._tmp, clock)
        self.assertIsNone(store.resolve("nope"))

    def test_a_revoked_key_401_within_cache_ttl_no_restart(self) -> None:
        # Two store instances share the file + clock: `app_store` is the
        # running app; `admin` is the out-of-process admin CLI. The revoke is
        # written by `admin` and must propagate to `app_store` within the
        # ≤5s cache-TTL with no restart (no new store object).
        clock = FakeClock()
        admin = self._store(self._tmp, clock)
        app_store = self._store(self._tmp, clock, ttl=5.0)
        admin.issue(token="tok", key_id="k1", tenant_id=TENANT_A, ttl_seconds=3600)

        self.assertIsNotNone(app_store.resolve("tok"))  # loads snapshot

        admin.revoke("k1")  # out-of-process mutation
        # Still inside the cache window → app has not reloaded yet.
        self.assertIsNotNone(app_store.resolve("tok"))
        # Advance past the cache-TTL → same object reloads → 401 (None).
        clock.advance(6.0)
        self.assertIsNone(app_store.resolve("tok"))

    def test_b_expired_key_resolves_none(self) -> None:
        clock = FakeClock()
        store = self._store(self._tmp, clock)
        store.issue(token="tok", key_id="k1", tenant_id=TENANT_A, ttl_seconds=100)
        self.assertIsNotNone(store.resolve("tok"))
        clock.advance(101)  # now > expires_at, evaluated on every resolve
        self.assertIsNone(store.resolve("tok"))

    def test_no_infinite_lived_keys(self) -> None:
        clock = FakeClock()
        store = self._store(self._tmp, clock)
        with self.assertRaises(ValueError):
            store.issue(token="tok", key_id="k1", tenant_id=TENANT_A, ttl_seconds=0)

    def test_c_rotation_grace_overlap_then_old_revoked(self) -> None:
        clock = FakeClock()
        admin = self._store(self._tmp, clock)
        app_store = self._store(self._tmp, clock, ttl=5.0)
        admin.issue(token="tok-old", key_id="k-old", tenant_id=TENANT_A, ttl_seconds=1000)

        admin.rotate(old_key_id="k-old", new_token="tok-new", new_key_id="k-new")
        clock.advance(6)  # let the app reload
        # Grace overlap: both keys valid, same tenant, until old expires_at.
        self.assertEqual(app_store.resolve("tok-old").tenant_id, TENANT_A)
        new_rec = app_store.resolve("tok-new")
        self.assertEqual(new_rec.tenant_id, TENANT_A)
        self.assertEqual(new_rec.rotated_from, "k-old")

        admin.revoke("k-old")  # cut over
        clock.advance(6)
        self.assertIsNone(app_store.resolve("tok-old"))  # old gone
        self.assertIsNotNone(app_store.resolve("tok-new"))  # new still valid

    def test_g_per_tenant_override_stored_and_resolved(self) -> None:
        clock = FakeClock()
        store = self._store(self._tmp, clock)
        store.issue(
            token="tok",
            key_id="k1",
            tenant_id=TENANT_A,
            ttl_seconds=3600,
            rate_limit={"scans_per_min": 2, "max_concurrent": 1},
        )
        rec = store.resolve("tok")
        cfg = RateLimitConfig.resolve(rec, RateLimitConfig(scans_per_min=10, max_concurrent=3))
        self.assertEqual(cfg.scans_per_min, 2)
        self.assertEqual(cfg.max_concurrent, 1)

    def test_cache_ttl_clamped_to_5s(self) -> None:
        store = KeyStore(self._tmp / "k.json", cache_ttl_seconds=3600, clock=FakeClock())
        self.assertLessEqual(store._cache_ttl, 5.0)


# ---------------------------------------------------------------------------
# D3 rate limiter — component
# ---------------------------------------------------------------------------


class RateLimiterTests(unittest.TestCase):
    def test_d_nth_plus_one_scan_per_min_rate_limited(self) -> None:
        clock = FakeClock()
        limiter = RateLimiter(clock=clock)
        cfg = RateLimitConfig(scans_per_min=3, max_concurrent=100)
        # N=3 admitted (leases released so concurrency is not the limiter).
        for _ in range(3):
            limiter.admit_scan(TENANT_A, cfg).release()
        with self.assertRaises(RateLimited) as ctx:
            limiter.admit_scan(TENANT_A, cfg)
        self.assertEqual(ctx.exception.reason, "scans_per_min")
        self.assertGreaterEqual(ctx.exception.retry_after, 1)

    def test_rate_bucket_refills_over_time(self) -> None:
        clock = FakeClock()
        limiter = RateLimiter(clock=clock)
        cfg = RateLimitConfig(scans_per_min=60, max_concurrent=100)  # 1 token/sec
        for _ in range(60):
            limiter.admit_scan(TENANT_A, cfg).release()
        with self.assertRaises(RateLimited):
            limiter.admit_scan(TENANT_A, cfg)
        clock.advance(1.0)  # one token refilled
        limiter.admit_scan(TENANT_A, cfg).release()  # succeeds

    def test_e_mth_plus_one_concurrent_rate_limited(self) -> None:
        clock = FakeClock()
        limiter = RateLimiter(clock=clock)
        cfg = RateLimitConfig(scans_per_min=1000, max_concurrent=2)
        held = [limiter.admit_scan(TENANT_A, cfg) for _ in range(2)]  # M in-flight
        with self.assertRaises(RateLimited) as ctx:
            limiter.admit_scan(TENANT_A, cfg)
        self.assertEqual(ctx.exception.reason, "max_concurrent")
        # Releasing one frees a slot.
        held[0].release()
        limiter.admit_scan(TENANT_A, cfg).release()

    def test_f_two_tenants_independent_buckets_not_ip(self) -> None:
        # Same limiter (same "source"): tenant A exhausted does not affect B.
        clock = FakeClock()
        limiter = RateLimiter(clock=clock)
        cfg = RateLimitConfig(scans_per_min=1, max_concurrent=100)
        limiter.admit_scan(TENANT_A, cfg).release()
        with self.assertRaises(RateLimited):
            limiter.admit_scan(TENANT_A, cfg)  # A depleted
        limiter.admit_scan(TENANT_B, cfg).release()  # B independent → OK

    def test_lease_released_once_idempotent(self) -> None:
        clock = FakeClock()
        limiter = RateLimiter(clock=clock)
        cfg = RateLimitConfig(scans_per_min=1000, max_concurrent=1)
        lease = limiter.admit_scan(TENANT_A, cfg)
        lease.release()
        lease.release()  # no-op, must not underflow the gauge
        limiter.admit_scan(TENANT_A, cfg).release()  # slot is free


# ---------------------------------------------------------------------------
# HTTP wiring — through create_app with the gateway active
# ---------------------------------------------------------------------------


class GatewayHttpTests(unittest.TestCase):
    """End-to-end proof that api.app honours the gateway on scan-creating
    routes. scan_runtime is left None so an *admitted* request returns 503
    (proving it passed auth + rate-limit) while rejected requests return
    401/429 — isolating the gateway behaviour from the scan pipeline."""

    def setUp(self) -> None:
        self.rubric = Rubric.load(RUBRIC_PATH)
        import tempfile

        self._tmp = Path(tempfile.mkdtemp())

    def _app(self, gateway: EdgeGateway):
        app = create_app(rubric=self.rubric, bundles=BundleStore(), scan_runtime=None)
        app.state.edge_gateway = gateway  # inject the deterministic gateway
        return TestClient(app, raise_server_exceptions=False)

    def _gateway(self, clock: FakeClock, defaults: RateLimitConfig) -> EdgeGateway:
        store = KeyStore(self._tmp / "keys.json", cache_ttl_seconds=5.0, clock=clock)
        return EdgeGateway(store=store, limiter=RateLimiter(clock=clock), defaults=defaults)

    def test_revoked_key_http_401(self) -> None:
        clock = FakeClock()
        gw = self._gateway(clock, RateLimitConfig(scans_per_min=100, max_concurrent=100))
        gw.store.issue(token="tok", key_id="k1", tenant_id=TENANT_A, ttl_seconds=3600)
        client = self._app(gw)

        ok = client.post("/scanner/v1/scans", json={}, headers={"Authorization": "Bearer tok"})
        self.assertEqual(ok.status_code, 503, ok.text)  # admitted, runtime unconfigured

        gw.store.revoke("k1")
        clock.advance(6)
        denied = client.post("/scanner/v1/scans", json={}, headers={"Authorization": "Bearer tok"})
        self.assertEqual(denied.status_code, 401, denied.text)

    def test_expired_key_http_401(self) -> None:
        clock = FakeClock()
        gw = self._gateway(clock, RateLimitConfig(scans_per_min=100, max_concurrent=100))
        gw.store.issue(token="tok", key_id="k1", tenant_id=TENANT_A, ttl_seconds=100)
        client = self._app(gw)
        self.assertEqual(
            client.post("/scanner/v1/scans", json={}, headers={"Authorization": "Bearer tok"}).status_code,
            503,
        )
        clock.advance(101)
        self.assertEqual(
            client.post("/scanner/v1/scans", json={}, headers={"Authorization": "Bearer tok"}).status_code,
            401,
        )

    def test_missing_bearer_http_401(self) -> None:
        clock = FakeClock()
        gw = self._gateway(clock, RateLimitConfig(scans_per_min=100, max_concurrent=100))
        gw.store.issue(token="tok", key_id="k1", tenant_id=TENANT_A, ttl_seconds=3600)
        client = self._app(gw)
        self.assertEqual(client.post("/scanner/v1/scans", json={}).status_code, 401)

    def test_d_http_rate_limit_429_with_retry_after(self) -> None:
        clock = FakeClock()
        gw = self._gateway(clock, RateLimitConfig(scans_per_min=3, max_concurrent=100))
        gw.store.issue(token="tok", key_id="k1", tenant_id=TENANT_A, ttl_seconds=3600)
        client = self._app(gw)
        h = {"Authorization": "Bearer tok"}
        for _ in range(3):  # N admitted → 503 (runtime None)
            self.assertEqual(client.post("/scanner/v1/scans", json={}, headers=h).status_code, 503)
        limited = client.post("/scanner/v1/scans", json={}, headers=h)  # N+1
        self.assertEqual(limited.status_code, 429, limited.text)
        self.assertIn("Retry-After", limited.headers)
        self.assertGreaterEqual(int(limited.headers["Retry-After"]), 1)
        self.assertEqual(limited.json()["error"], "rate_limited")

    def test_e_http_concurrency_limit_429(self) -> None:
        clock = FakeClock()
        gw = self._gateway(clock, RateLimitConfig(scans_per_min=1000, max_concurrent=1))
        rec = gw.store.issue(token="tok", key_id="k1", tenant_id=TENANT_A, ttl_seconds=3600)
        # Pre-seed one in-flight lease == a concurrent scan already running.
        held = gw.admit_scan(rec)  # noqa: F841 — kept un-released on purpose
        client = self._app(gw)
        limited = client.post(
            "/scanner/v1/scans", json={}, headers={"Authorization": "Bearer tok"}
        )
        self.assertEqual(limited.status_code, 429, limited.text)
        self.assertEqual(limited.json()["reason"], "max_concurrent")
        self.assertIn("Retry-After", limited.headers)

    def test_f_http_two_tenants_same_ip_independent(self) -> None:
        clock = FakeClock()
        gw = self._gateway(clock, RateLimitConfig(scans_per_min=1000, max_concurrent=1))
        rec_a = gw.store.issue(token="tok-a", key_id="ka", tenant_id=TENANT_A, ttl_seconds=3600)
        gw.store.issue(token="tok-b", key_id="kb", tenant_id=TENANT_B, ttl_seconds=3600)
        held_a = gw.admit_scan(rec_a)  # noqa: F841 — A now at max_concurrent
        client = self._app(gw)  # same TestClient == same source
        a = client.post("/scanner/v1/scans", json={}, headers={"Authorization": "Bearer tok-a"})
        self.assertEqual(a.status_code, 429, a.text)  # A limited
        b = client.post("/scanner/v1/scans", json={}, headers={"Authorization": "Bearer tok-b"})
        self.assertEqual(b.status_code, 503, b.text)  # B independent → admitted

    def test_g_http_per_tenant_override_more_permissive(self) -> None:
        clock = FakeClock()
        gw = self._gateway(clock, RateLimitConfig(scans_per_min=1, max_concurrent=100))
        # Tenant A override raises its ceiling above the default of 1.
        gw.store.issue(
            token="tok-a",
            key_id="ka",
            tenant_id=TENANT_A,
            ttl_seconds=3600,
            rate_limit={"scans_per_min": 3, "max_concurrent": 100},
        )
        # Tenant B keeps the default of 1.
        gw.store.issue(token="tok-b", key_id="kb", tenant_id=TENANT_B, ttl_seconds=3600)
        client = self._app(gw)
        ha, hb = {"Authorization": "Bearer tok-a"}, {"Authorization": "Bearer tok-b"}
        for _ in range(3):  # A honoured to 3
            self.assertEqual(client.post("/scanner/v1/scans", json={}, headers=ha).status_code, 503)
        self.assertEqual(client.post("/scanner/v1/scans", json={}, headers=ha).status_code, 429)
        # B hits its default ceiling after 1.
        self.assertEqual(client.post("/scanner/v1/scans", json={}, headers=hb).status_code, 503)
        self.assertEqual(client.post("/scanner/v1/scans", json={}, headers=hb).status_code, 429)

    def test_gateway_disabled_is_legacy_path(self) -> None:
        # No gateway on app.state ⇒ flat-dict/no-auth legacy behaviour intact.
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {}, clear=False):
            app = create_app(rubric=self.rubric, bundles=BundleStore(), scan_runtime=None)
            self.assertIsNone(app.state.edge_gateway)


if __name__ == "__main__":
    unittest.main()
