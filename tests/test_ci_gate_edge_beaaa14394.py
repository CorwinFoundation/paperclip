"""BEAAA-14394 (fix 1) — CI gate verdicts restored at the real API edge.

Regression proofs for the reviewed defect: a completed scan could never return
a real ``gate_pass``/``gate_fail`` through FastAPI because the threshold source
was not wired through ``api/app.py`` and the gate was keyed by ``scan_id``
instead of the authenticated tenant, ignoring the contracted policy band.

Contract: are-scanner-api-v1@v1.0 (score envelope byte-unchanged — this fix is
edge wiring only; N=5 golden delta is 0 by construction, no scoring path is
touched). CEO ruling BEAAA-11725: default gate = the vendor's contracted policy
band looked up by *account/tenant*; ``fail_below_score`` is a stricter-only
override.
"""
from __future__ import annotations

import types
import unittest

from api.ci_scan_ext import (
    DISPOSITION_GATE_FAIL,
    DISPOSITION_GATE_PASS,
    DISPOSITION_INFRA_RETRY,
    POLICY_BAND_FLOOR,
    policy_band_floor,
    resolve_gate_threshold,
)
from api.scan_handlers import ScanRuntime, scan_status_handler
from scanners.live_probe_orchestrator import DimResult, ScanResult

TENANT = "tenant-acct-42"


class _FakeScoreLog:
    """Minimal scan_log stand-in: ``get`` returns a row with ``score_json``."""

    def __init__(self, score_json: dict) -> None:
        self._score_json = score_json

    def get(self, _agent_id: str, _run_timestamp: str) -> dict:
        return {"score_json": dict(self._score_json)}


def _completed_result(scan_id: str) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status="complete",
        scanner_version="0.3.0",
        corpus_version="0.1.0",
        rows_emitted=10,
        k_replicates=5,
        live_probe_variance_band=None,
        dim_results=(
            DimResult(dim_id="P1.1", raw=4.5, spread=0.0, replicates=(4.5,) * 5),
        ),
        spread_per_dim={"P1.1": 0.0},
        pillar_spread={"P1": 0.0},
        total_spread=0.0,
        incomplete=False,
        incomplete_reason=None,
    )


def _incomplete_result(scan_id: str) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status="incomplete",
        scanner_version="0.3.0",
        corpus_version="0.1.0",
        rows_emitted=0,
        k_replicates=5,
        live_probe_variance_band=None,
        dim_results=(),
        spread_per_dim={},
        pillar_spread={},
        total_spread=0.0,
        incomplete=True,
        incomplete_reason="orchestrator_timeout",
    )


def _runtime_with_score(
    scan_id: str,
    *,
    are_total: float | None,
    band: str | None,
    tenant_id: str = TENANT,
    incomplete: bool = False,
) -> ScanRuntime:
    result = _incomplete_result(scan_id) if incomplete else _completed_result(scan_id)

    def run_fn(_req, _secret):  # pragma: no cover - never invoked in these tests
        return result

    rt = ScanRuntime(run_scan_fn=run_fn)
    rt._results[scan_id] = result
    rt._scan_requests[scan_id] = types.SimpleNamespace(tenant_id=tenant_id)
    rt._kickoff_keys[scan_id] = ("agent-x", "2026-07-15T00:00:00Z")
    score_json: dict = {}
    if band is not None:
        score_json["band"] = band
    if are_total is not None:
        score_json["are_total"] = are_total
    rt._scan_log = _FakeScoreLog(score_json)
    return rt


class ResolveThresholdTests(unittest.TestCase):
    def test_band_floor_table_is_the_single_source(self) -> None:
        # ci_plugin must re-export the same object — no divergent floor table.
        from ci_plugin.client import BAND_FLOOR

        self.assertIs(BAND_FLOOR, POLICY_BAND_FLOOR)

    def test_policy_band_floor_maps_letters(self) -> None:
        self.assertEqual(policy_band_floor("B"), 70.0)
        self.assertEqual(policy_band_floor("a"), 85.0)  # case-insensitive
        self.assertIsNone(policy_band_floor("Z"))
        self.assertIsNone(policy_band_floor(None))

    def test_stricter_override_wins_looser_ignored(self) -> None:
        self.assertEqual(resolve_gate_threshold("B", 80.0), 80.0)  # stricter
        self.assertEqual(resolve_gate_threshold("B", 60.0), 70.0)  # looser -> band
        self.assertEqual(resolve_gate_threshold("B", None), 70.0)  # band only
        self.assertEqual(resolve_gate_threshold(None, 65.0), 65.0)  # override only
        self.assertIsNone(resolve_gate_threshold(None, None))  # un-gateable


class CiGateDispositionTests(unittest.TestCase):
    def test_completed_high_score_returns_gate_pass(self) -> None:
        rt = _runtime_with_score("scan-hi", are_total=88, band="B")
        _, payload = scan_status_handler(
            "scan-hi", runtime=rt, audience="ci_plugin",
            authenticated_tenant_id=TENANT,
            ci_threshold_lookup=lambda _t: ("B", None),
        )
        self.assertEqual(payload["disposition"], DISPOSITION_GATE_PASS)

    def test_completed_low_score_returns_gate_fail(self) -> None:
        rt = _runtime_with_score("scan-lo", are_total=60, band="C")
        _, payload = scan_status_handler(
            "scan-lo", runtime=rt, audience="ci_plugin",
            authenticated_tenant_id=TENANT,
            ci_threshold_lookup=lambda _t: ("B", None),
        )
        self.assertEqual(payload["disposition"], DISPOSITION_GATE_FAIL)

    def test_band_floor_enforced_without_numeric_override(self) -> None:
        # Fails before the fix: old edge only honoured ``fail_below`` and
        # ignored the contracted band, so are_total=65 vs band B floor 70
        # produced infra_retry instead of gate_fail.
        rt = _runtime_with_score("scan-band", are_total=65, band="C")
        _, payload = scan_status_handler(
            "scan-band", runtime=rt, audience="ci_plugin",
            authenticated_tenant_id=TENANT,
            ci_threshold_lookup=lambda _t: ("B", None),
        )
        self.assertEqual(payload["disposition"], DISPOSITION_GATE_FAIL)

    def test_stricter_numeric_override_tightens_gate(self) -> None:
        # are_total=75 clears band B (70) but not the stricter override (80).
        rt = _runtime_with_score("scan-strict", are_total=75, band="B")
        _, payload = scan_status_handler(
            "scan-strict", runtime=rt, audience="ci_plugin",
            authenticated_tenant_id=TENANT,
            ci_threshold_lookup=lambda _t: ("B", 80.0),
        )
        self.assertEqual(payload["disposition"], DISPOSITION_GATE_FAIL)

    def test_missing_threshold_source_is_infra_retry(self) -> None:
        rt = _runtime_with_score("scan-nosrc", are_total=88, band="A")
        _, payload = scan_status_handler(
            "scan-nosrc", runtime=rt, audience="ci_plugin",
            authenticated_tenant_id=TENANT,
            ci_threshold_lookup=None,
        )
        self.assertEqual(payload["disposition"], DISPOSITION_INFRA_RETRY)

    def test_incomplete_scan_is_infra_retry(self) -> None:
        rt = _runtime_with_score(
            "scan-inc", are_total=None, band=None, incomplete=True
        )
        _, payload = scan_status_handler(
            "scan-inc", runtime=rt, audience="ci_plugin",
            authenticated_tenant_id=TENANT,
            ci_threshold_lookup=lambda _t: ("A", None),
        )
        self.assertEqual(payload["disposition"], DISPOSITION_INFRA_RETRY)

    def test_threshold_lookup_keyed_by_tenant_not_scan_id(self) -> None:
        # Fails before the fix: the edge keyed the lookup by scan_id.
        rt = _runtime_with_score("scan-key", are_total=88, band="B")
        seen: list[str] = []

        def lookup(key: str):
            seen.append(key)
            return ("B", None)

        scan_status_handler(
            "scan-key", runtime=rt, audience="ci_plugin",
            authenticated_tenant_id=TENANT,
            ci_threshold_lookup=lookup,
        )
        self.assertEqual(seen, [TENANT])
        self.assertNotIn("scan-key", seen)


class FastApiEdgeTests(unittest.TestCase):
    """Proves ``api/app.py`` actually threads the threshold source through the
    real FastAPI GET edge — the specific wiring that was missing."""

    def _client(self, ci_threshold_source):
        try:
            from fastapi.testclient import TestClient
        except ImportError as exc:  # pragma: no cover
            self.skipTest(f"fastapi not installed: {exc}")
        from pathlib import Path

        from api.app import create_app
        from api.bundle import BundleStore
        from rubric.scoring import Rubric

        repo = Path(__file__).resolve().parents[1]
        rt = _runtime_with_score("scan-edge", are_total=88, band="B")
        app = create_app(
            rubric=Rubric.load(repo / "rubric" / "rubric.yaml"),
            bundles=BundleStore(),
            scan_runtime=rt,
            ci_threshold_source=ci_threshold_source,
        )
        return TestClient(app)

    def test_get_ci_plugin_returns_real_gate_verdict(self) -> None:
        # Fails before the fix: app.py never passed ci_threshold_lookup, so the
        # ci_plugin audience always returned infra_retry regardless of score.
        client = self._client(lambda _tenant: ("B", None))
        resp = client.get("/scan/scan-edge?audience=ci_plugin")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["disposition"], DISPOSITION_GATE_PASS)

    def test_get_ci_plugin_no_source_is_infra_retry(self) -> None:
        client = self._client(None)
        resp = client.get("/scan/scan-edge?audience=ci_plugin")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["disposition"], DISPOSITION_INFRA_RETRY)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
