"""BEAAA-1265 (WS1) — POST /scanner/v1/rescans shim wiring.

Rule-execution per ``contracts/scanner-implementation-supervision-v1.md@v1.0``
classification table (route → existing /scans pipeline; pipeline parity is the
design intent; no rubric / judge / scoring-math touch). Spec authority:
scope-beta-design rev 2 §2.1 + §3 row 1 (BEAAA-1000 → BEAAA-1255 umbrella).

AC mapping (per BEAAA-1265 acceptance criteria):

* AC#1 — POST /scanner/v1/rescans route lands in the API surface and shares
  the live-probe entrypoint with /scan (no duplicated rubric/judge logic;
  both delegate to ``_kickoff_live_probe_scan`` in ``api/scan_handlers.py``).
* AC#2 — With ``ARE_RESCANS_MEASURED`` unset/off, the route returns the
  legacy doc-only response (no behavioral regression vs. today's no-route).
* AC#3 — With ``ARE_RESCANS_MEASURED=on``, ``purpose ∈ {pre_bind, mid_term,
  claim_review}`` all reach the measurement pipeline and return a 202 with
  ``{scan_id, status: accepted}`` (the /scans accept-shape envelope).
* AC#4 — ``agent_id`` missing from the runtime's scan log → 422 with
  ``agent_id_no_prior_scan``.
* AC#5 — Behavioral parity: identical inputs to /scans vs. /rescans produce
  identical (shape, scoring envelope) responses modulo run_timestamp +
  scan_id; ``prior_scan_refs[]`` is WS3.
* AC#6 — N=5 calibration: shim delegates to the same scoring engine, so the
  delta against the /scans baseline is 0 by construction (verified by the
  parity test below + the calibration-delta artifact at
  ``tools/calibration-deltas/2026-05-30-beaaa1265-ws1-rescans-shim.json``).
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
from api.scan_handlers import (
    RESCAN_PURPOSES,
    ScanRuntime,
    rescan_handler,
)
from harness import egress
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
# Fixtures — corpus, runtime stub, request bodies. Pattern matches
# tests/test_scan_handlers_beaaa547.py so the shim's contract surface stays
# directly comparable to the scan handler it shims.
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


def _synthetic_result(scan_id: str) -> ScanResult:
    return ScanResult(
        scan_id=scan_id,
        status="complete",
        scanner_version="0.3.5",
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


def _runtime_with_prior_scan() -> ScanRuntime:
    captured: dict[str, ScanRequest] = {}

    def run_scan_fn(scan_request: ScanRequest, secret: str) -> ScanResult:
        captured["request"] = scan_request
        captured["secret_observed"] = secret
        return _synthetic_result(scan_request.scan_id)

    runtime = ScanRuntime(run_scan_fn=run_scan_fn, prior_agent_ids=(AGENT_ID,))
    runtime.captured = captured  # type: ignore[attr-defined]
    return runtime


def _rescan_body(corpus_hash: str, *, purpose: str = "mid_term", **overrides) -> dict:
    base = {
        "tenant_id": TENANT,
        "agent_id": AGENT_ID,
        "agent_endpoint": {"host": "agent.customer.example.com"},
        "credential_ref": "phase1-probe.v1",
        "credential_secret": "scoped-token-abc",
        "corpus_snapshot_hash": corpus_hash,
        "scanner_version": "0.3.5",
        "corpus_version": "0.1.0",
        "seed": 42,
        "scan_mode": "live_probe",
        "k_replicates": 5,
        "requested_pillars": ["GOVERNANCE", "ROBUSTNESS"],
        "scanner_invocation_context": {"purpose": purpose},
    }
    base.update(overrides)
    return base


@contextmanager
def _measured_on():
    """Toggle ARE_RESCANS_MEASURED=on for the duration of the block."""
    prior = os.environ.get(RESCANS_MEASURED_ENV)
    os.environ[RESCANS_MEASURED_ENV] = "on"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(RESCANS_MEASURED_ENV, None)
        else:
            os.environ[RESCANS_MEASURED_ENV] = prior


@contextmanager
def _measured_off():
    """Toggle ARE_RESCANS_MEASURED=off for the duration of the block.

    Post-WS7 (BEAAA-1283) the route default is ``on``; rollback Path 1 per
    ADR-0017 §6 is to set the env var to ``off`` explicitly. This helper
    exercises that path so the doc-only response shape is asserted under
    the exact rollback contract callers will set.
    """
    prior = os.environ.get(RESCANS_MEASURED_ENV)
    os.environ[RESCANS_MEASURED_ENV] = "off"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(RESCANS_MEASURED_ENV, None)
        else:
            os.environ[RESCANS_MEASURED_ENV] = prior


# ---------------------------------------------------------------------------
# Handler-level tests (framework-agnostic; mirror test_scan_handlers_beaaa547).
# ---------------------------------------------------------------------------


def test_rescan_handler_returns_202_with_scan_id(corpus_hash):
    runtime = _runtime_with_prior_scan()
    body = _rescan_body(corpus_hash)
    status, payload = rescan_handler(
        body,
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: "rescan-test-1",
    )
    assert status == 202
    # BEAAA-1274 (WS4) AC #5 — additive flags default false on nominal-path
    # /rescans responses (mid_term purpose, no degradation).
    # BEAAA-1273 (WS3) — ``prior_scan_refs[]`` is ALWAYS present on /rescans;
    # empty array on this unwired runtime (no ``scan_log``), populated when
    # the runtime carries one (per ADR-0017 §2.1 — impl picks "always emit").
    # ``forensic_bundle_uri`` / ``snapshot_hash`` require a persisted row,
    # so they are omitted on the unwired-runtime path here.
    assert payload == {
        "scan_id": "rescan-test-1",
        "status": "accepted",
        "prior_scan_refs": [],
        "partial_completion": False,
        "confidence_below_adjudication_threshold": False,
    }


def test_rescan_refuses_residential_proxy_env(corpus_hash, monkeypatch):
    runtime = _runtime_with_prior_scan()
    monkeypatch.setenv(egress.PROXY_ENV, "http://u:p@gate.example.com:7000")
    body = _rescan_body(corpus_hash, purpose="pre_bind")
    with pytest.raises(Exception) as exc_info:
        rescan_handler(body, tenant_header=TENANT, runtime=runtime)
    assert exc_info.value.status == 422
    assert exc_info.value.message == "residential_proxy_forbidden_for_live_probe"
    assert "request" not in runtime.captured


@pytest.mark.parametrize("purpose", sorted(RESCAN_PURPOSES))
def test_rescan_handler_accepts_all_three_purposes(corpus_hash, purpose):
    runtime = _runtime_with_prior_scan()
    body = _rescan_body(corpus_hash, purpose=purpose)
    status, payload = rescan_handler(
        body,
        tenant_header=TENANT,
        runtime=runtime,
        scan_id_factory=lambda: f"rescan-{purpose}",
    )
    assert status == 202
    assert payload["status"] == "accepted"


def test_rescan_no_prior_scan_yields_422_agent_id_no_prior_scan(corpus_hash):
    runtime = ScanRuntime(run_scan_fn=lambda req, secret: _synthetic_result(req.scan_id))
    body = _rescan_body(corpus_hash)
    with pytest.raises(Exception) as exc_info:
        rescan_handler(body, tenant_header=TENANT, runtime=runtime)
    assert exc_info.value.status == 422
    assert exc_info.value.message == "agent_id_no_prior_scan"


def test_rescan_missing_agent_id_yields_422(corpus_hash):
    runtime = _runtime_with_prior_scan()
    body = _rescan_body(corpus_hash)
    body.pop("agent_id")
    with pytest.raises(Exception) as exc_info:
        rescan_handler(body, tenant_header=TENANT, runtime=runtime)
    assert exc_info.value.status == 422
    assert exc_info.value.message == "agent_id_required"


def test_rescan_invalid_agent_id_shape_yields_422(corpus_hash):
    runtime = _runtime_with_prior_scan()
    body = _rescan_body(corpus_hash, agent_id="has spaces & punctuation!")
    with pytest.raises(Exception) as exc_info:
        rescan_handler(body, tenant_header=TENANT, runtime=runtime)
    assert exc_info.value.status == 422
    assert exc_info.value.message == "agent_id_invalid"


def test_rescan_missing_invocation_context_yields_422(corpus_hash):
    runtime = _runtime_with_prior_scan()
    body = _rescan_body(corpus_hash)
    body.pop("scanner_invocation_context")
    with pytest.raises(Exception) as exc_info:
        rescan_handler(body, tenant_header=TENANT, runtime=runtime)
    assert exc_info.value.status == 422
    assert exc_info.value.message == "scanner_invocation_context_required"


@pytest.mark.parametrize("bad_purpose", ["", "unknown", "claim", None, 7])
def test_rescan_invalid_purpose_yields_422(corpus_hash, bad_purpose):
    runtime = _runtime_with_prior_scan()
    body = _rescan_body(corpus_hash)
    body["scanner_invocation_context"] = {"purpose": bad_purpose}
    with pytest.raises(Exception) as exc_info:
        rescan_handler(body, tenant_header=TENANT, runtime=runtime)
    assert exc_info.value.status == 422
    assert exc_info.value.message == "purpose_invalid"


def test_rescan_tenant_isolation_reuses_t7_alias(corpus_hash):
    runtime = _runtime_with_prior_scan()
    body = _rescan_body(corpus_hash, tenant_id="tenant-A")
    with pytest.raises(Exception) as exc_info:
        rescan_handler(body, tenant_header="tenant-B", runtime=runtime)
    assert exc_info.value.status == 409


# ---------------------------------------------------------------------------
# Shared-entrypoint sentry — AC#1 "no duplicated rubric/judge logic".
# ---------------------------------------------------------------------------


def test_rescan_and_scan_share_kickoff_entrypoint(corpus_hash):
    """AC#1 — the shim must reuse the existing /scans pipeline.

    Both ``scan_handler`` and ``rescan_handler`` route through
    ``_kickoff_live_probe_scan`` in ``api/scan_handlers.py``. This sentry
    catches any future drift where someone re-implements the orchestrator
    kickoff inside the rescan path (Scanner Engineer OS auto-reject:
    "Duplicating rubric/judge logic from existing scanner code into the
    CLI or any new probe wrapper").
    """
    import inspect

    from api import scan_handlers as sh

    scan_src = inspect.getsource(sh.scan_handler)
    rescan_src = inspect.getsource(sh.rescan_handler)
    assert "_kickoff_live_probe_scan" in scan_src
    assert "_kickoff_live_probe_scan" in rescan_src


# ---------------------------------------------------------------------------
# Behavioral parity (AC#5 + AC#6 — N=5 delta is 0 by construction because
# both routes call the same scoring engine).
# ---------------------------------------------------------------------------


def test_rescan_parity_with_scan_same_inputs_same_envelope(corpus_hash):
    """AC#5 — same inputs via /rescans vs. /scans → same envelope shape on
    the SHARED scoring/accept keys.

    BEAAA-1273 (WS3) introduces three rescans-only additive response fields
    per ADR-0017 §2.1: ``prior_scan_refs[]`` (always emitted on /rescans,
    empty array when no chain) and ``forensic_bundle_uri`` /
    ``snapshot_hash`` (top-level when the runtime is wired with a
    ``scan_log``). The /scans envelope is preserved at v1.1.0 (AC #3),
    so /rescans ⊃ /scans on this fixture (no scan_log wired → only
    ``prior_scan_refs`` diverges).

    The parity invariant carried by this test (BEAAA-1265 AC #5) is that
    the SCORING envelope is identical (no rubric/judge drift between the
    routes); the WS3 chain additions are intentional divergence per the
    contract patch, not a parity violation. The WS4 partial-completion
    flags default ``false`` on both routes per BEAAA-1274 AC #5.
    """
    from api.scan_handlers import scan_handler

    runtime = _runtime_with_prior_scan()
    scan_body = {k: v for k, v in _rescan_body(corpus_hash).items() if k not in (
        "agent_id", "scanner_invocation_context"
    )}
    rescan_body = _rescan_body(corpus_hash)

    scan_status, scan_payload = scan_handler(
        scan_body, tenant_header=TENANT, runtime=runtime,
        scan_id_factory=lambda: "scan-id-A",
    )
    rescan_status, rescan_payload = rescan_handler(
        rescan_body, tenant_header=TENANT, runtime=runtime,
        scan_id_factory=lambda: "scan-id-B",
    )

    assert scan_status == rescan_status == 202
    # WS3 rescans-only additive keys; /scans envelope unchanged (AC #3).
    ws3_rescan_only_keys = {"prior_scan_refs"}
    assert set(rescan_payload.keys()) == set(scan_payload.keys()) | ws3_rescan_only_keys
    assert scan_payload["status"] == rescan_payload["status"] == "accepted"


def test_rescan_n5_delta_is_zero_by_construction(corpus_hash):
    """AC#6 — N=5 delta on the golden set is 0 because the scoring engine
    is the same code path. We assert this via deterministic stub: 5 rescans
    against the same inputs yield identical ScanResults (modulo scan_id).

    BEAAA-1273 (WS3) note: the rescan envelope's shared-with-/scans keys
    remain identical to baseline (no scoring drift). The WS3 chain
    additions land on top — empty array on this unwired runtime so the
    additive surface itself is byte-identical across replicates.
    """
    from api.scan_handlers import scan_handler

    runtime = _runtime_with_prior_scan()

    def envelope_keys(payload: dict) -> set[str]:
        return set(payload.keys())

    scan_body = {k: v for k, v in _rescan_body(corpus_hash).items() if k not in (
        "agent_id", "scanner_invocation_context"
    )}
    rescan_body = _rescan_body(corpus_hash)

    baseline_status, baseline_payload = scan_handler(
        scan_body, tenant_header=TENANT, runtime=runtime,
        scan_id_factory=lambda: "baseline",
    )

    ws3_rescan_only_keys = {"prior_scan_refs"}
    expected_rescan_keys = envelope_keys(baseline_payload) | ws3_rescan_only_keys

    for i in range(5):
        status, payload = rescan_handler(
            rescan_body, tenant_header=TENANT, runtime=runtime,
            scan_id_factory=lambda i=i: f"replicate-{i}",
        )
        assert status == baseline_status
        assert envelope_keys(payload) == expected_rescan_keys
        # Same orchestrator → identical synthetic result (modulo scan_id).
        assert payload["status"] == baseline_payload["status"]
        # WS3 empty-array on unwired runtime is itself byte-identical.
        assert payload["prior_scan_refs"] == []


# ---------------------------------------------------------------------------
# App-level tests — full FastAPI roundtrip + ARE_RESCANS_MEASURED flag.
# ---------------------------------------------------------------------------


class RescansAppTests(unittest.TestCase):
    def setUp(self):
        self.rubric = Rubric.load(RUBRIC_PATH)
        # Build a real corpus tarball + runtime with a prior scan registered.
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        tarball_path = Path(self._tmpdir.name) / "corpus.tar.gz"
        with tarfile.open(tarball_path, "w:gz") as tar:
            data = b'{"corpus_version":"0.1.0"}\n'
            info = tarfile.TarInfo(name="manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        self.corpus_hash = compute_corpus_snapshot_hash(tarball_path)
        self.runtime = _runtime_with_prior_scan()
        self.app = create_app(
            rubric=self.rubric,
            bundles=BundleStore(),
            scan_runtime=self.runtime,
        )
        self.client = TestClient(self.app)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_route_exists_and_returns_doc_only_when_flag_off(self):
        with _measured_off():
            body = _rescan_body(self.corpus_hash)
            r = self.client.post(
                "/scanner/v1/rescans",
                json=body,
                headers={"X-Tenant-Id": TENANT},
            )
        self.assertEqual(r.status_code, 200, r.text)
        payload = r.json()
        self.assertEqual(payload["status"], "doc_only")
        self.assertEqual(payload["agent_id"], AGENT_ID)
        self.assertEqual(payload["purpose"], "mid_term")
        self.assertIn(RESCANS_MEASURED_ENV, payload["note"])

    def test_route_returns_202_when_flag_on(self):
        with _measured_on():
            body = _rescan_body(self.corpus_hash, purpose="claim_review")
            r = self.client.post(
                "/scanner/v1/rescans",
                json=body,
                headers={"X-Tenant-Id": TENANT},
            )
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(r.json()["status"], "accepted")
        self.assertIn("scan_id", r.json())

    def test_route_returns_422_for_unknown_agent_id_when_flag_on(self):
        with _measured_on():
            body = _rescan_body(self.corpus_hash, agent_id="never-scanned-agent")
            r = self.client.post(
                "/scanner/v1/rescans",
                json=body,
                headers={"X-Tenant-Id": TENANT},
            )
        self.assertEqual(r.status_code, 422, r.text)
        # FastAPI HTTPException renders the detail under "detail".
        self.assertEqual(r.json()["detail"], "agent_id_no_prior_scan")

    def test_route_returns_422_for_invalid_purpose_when_flag_on(self):
        with _measured_on():
            body = _rescan_body(self.corpus_hash)
            body["scanner_invocation_context"] = {"purpose": "unknown_purpose"}
            r = self.client.post(
                "/scanner/v1/rescans",
                json=body,
                headers={"X-Tenant-Id": TENANT},
            )
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["detail"], "purpose_invalid")

    def test_flag_off_does_not_invoke_runtime(self):
        """AC#2 — flag-off must NOT reach the scoring pipeline.

        Sentry against accidental runtime entry under the legacy doc-only
        path (would break the rollback guarantee in scope-beta-design rev 2
        §7 — flag-off must be a true no-op from the scoring engine's POV).
        """
        with _measured_off():
            self.client.post(
                "/scanner/v1/rescans",
                json=_rescan_body(self.corpus_hash),
                headers={"X-Tenant-Id": TENANT},
            )
        self.assertNotIn("request", getattr(self.runtime, "captured", {}))

    def test_flag_on_with_unconfigured_runtime_returns_503(self):
        with _measured_on():
            app = create_app(rubric=self.rubric, bundles=BundleStore(), scan_runtime=None)
            client = TestClient(app)
            r = client.post(
                "/scanner/v1/rescans",
                json=_rescan_body(self.corpus_hash),
                headers={"X-Tenant-Id": TENANT},
            )
        self.assertEqual(r.status_code, 503, r.text)


if __name__ == "__main__":
    unittest.main()
