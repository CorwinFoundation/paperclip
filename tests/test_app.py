"""End-to-end tests for the FastAPI app (smoke + red-line wiring)."""
from __future__ import annotations

import anyio
import asyncio
import time
import unittest
from pathlib import Path

import httpx

try:
    from fastapi.testclient import TestClient
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest(f"fastapi not installed: {exc}")

from api.app import create_app
from api.bundle import BundleStore
from api.scan_handlers import ScanRuntime
from api.intake_guard import HOSTED_RUNNER
from rubric.scoring import Rubric
from scanners.live_probe_orchestrator import (
    LIVE_PROBE_VARIANCE_BAND,
    DimResult,
    ScanRequest,
    ScanResult,
)

REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"

TENANT = "11111111-1111-1111-1111-111111111111"
RUN_ID = "22222222-2222-2222-2222-222222222222"
SNAPSHOT_HASH = "sha256:" + "a" * 64
CORPUS_HASH = "sha256:" + "b" * 64


def _request(rubric: Rubric, raw: float = 2.0) -> dict:
    return {
        "tenant_id": TENANT,
        "snapshot": {
            "model_id": "acme-llm",
            "model_version": "v1.2.3",
            "prompt_version": "p7",
            "snapshot_hash": SNAPSHOT_HASH,
        },
        "eval_outputs": {
            did: {
                "raw": raw,
                "evidence_uri": f"https://evidence.local/{did}",
                "runner": HOSTED_RUNNER,
                "eval_run_id": RUN_ID,
            }
            for did in rubric.active_dim_ids
        },
    }


def _scan_request_body() -> dict:
    return {
        "tenant_id": TENANT,
        "agent_endpoint": {"host": "agent.customer.example.com"},
        "credential_ref": "phase1-probe.v1",
        "credential_secret": "scoped-token-abc",
        "corpus_snapshot_hash": CORPUS_HASH,
        "scanner_version": "0.3.7",
        "corpus_version": "0.1.0",
        "seed": 42,
        "scan_mode": "live_probe",
        "k_replicates": 5,
        "requested_pillars": ["GOVERNANCE", "ROBUSTNESS"],
    }


def _scan_result(scan_id: str) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status="complete",
        scanner_version="0.3.7",
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
        incomplete=False,
        incomplete_reason=None,
    )


class AppTests(unittest.TestCase):
    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        self.app = create_app(rubric=self.rubric, bundles=BundleStore())
        self.client = TestClient(self.app)

    def test_score_then_fetch_bundle(self):
        r = self.client.post(
            "/score",
            json=_request(self.rubric),
            headers={"X-Tenant-Id": TENANT},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        bundle_id = body["forensic_bundle_uri"].rsplit("/", 1)[-1]
        b = self.client.get(f"/forensic_bundle/{bundle_id}")
        self.assertEqual(b.status_code, 200, b.text)
        self.assertEqual(b.json()["bundle_id"], bundle_id)

    def test_score_tenant_mismatch_409(self):
        r = self.client.post(
            "/score",
            json=_request(self.rubric),
            headers={"X-Tenant-Id": "33333333-3333-3333-3333-333333333333"},
        )
        self.assertEqual(r.status_code, 409, r.text)

    def test_score_bad_snapshot_422(self):
        body = _request(self.rubric)
        body["snapshot"]["snapshot_hash"] = "bogus"
        r = self.client.post("/score", json=body, headers={"X-Tenant-Id": TENANT})
        self.assertEqual(r.status_code, 422, r.text)

    def test_score_self_reported_eval_422(self):
        body = _request(self.rubric)
        body["eval_outputs"]["P1.1"]["runner"] = "customer-self-reported"
        r = self.client.post("/score", json=body, headers={"X-Tenant-Id": TENANT})
        self.assertEqual(r.status_code, 422, r.text)

    def test_unknown_bundle_404(self):
        r = self.client.get("/forensic_bundle/00000000-0000-0000-0000-000000000000")
        self.assertEqual(r.status_code, 404)


def test_sla_route_responds_while_scan_kickoff_is_blocked():
    """BEAAA-7837 — app routes must not share /scan's blocking call path."""

    def slow_run_scan(scan_request: ScanRequest, secret: str) -> ScanResult:
        time.sleep(0.3)
        return _scan_result(scan_request.scan_id)

    app = create_app(
        rubric=Rubric.load(RUBRIC_PATH),
        bundles=BundleStore(),
        scan_runtime=ScanRuntime(run_scan_fn=slow_run_scan),
    )

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            scan_task = asyncio.create_task(
                client.post(
                    "/scan",
                    json=_scan_request_body(),
                    headers={"X-Tenant-Id": TENANT},
                )
            )
            try:
                await anyio.sleep(0.05)
                assert not scan_task.done()
                started = time.perf_counter()
                sla_response = await client.get("/scanner/v1/sla")
                elapsed = time.perf_counter() - started
                assert sla_response.status_code == 200, sla_response.text
                assert elapsed < 0.2
                scan_response = await scan_task
                assert scan_response.status_code == 202, scan_response.text
            finally:
                if not scan_task.done():
                    scan_task.cancel()

    anyio.run(exercise)


if __name__ == "__main__":
    unittest.main()
