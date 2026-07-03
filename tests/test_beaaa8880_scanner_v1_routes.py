"""BEAAA-11608 — BackBond Scanner v1 route provenance hardening."""
from __future__ import annotations

import io
import tarfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest(f"fastapi not installed: {exc}")

from api.app import create_app
from api.bundle import BundleStore
from api.scan_runtime_factory import make_production_scan_runtime
from rubric.scoring import Rubric
from scanners.snapshot_hash import compute_corpus_snapshot_hash


REPO = Path(__file__).resolve().parents[1]
RUBRIC_PATH = REPO / "rubric" / "rubric.yaml"
TENANT = "tenant-001"


def _corpus_tarball(tmp_path: Path) -> Path:
    tarball_path = tmp_path / "corpus.tar.gz"
    with tarfile.open(tarball_path, "w:gz") as tar:
        data = b'{"corpus_version":"0.1.0","seed":42}\n'
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return tarball_path


def _body(corpus_hash: str) -> dict:
    return {
        "tenant_id": TENANT,
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


def _client_for_corpus(tarball_path: Path) -> TestClient:
    runtime = make_production_scan_runtime(
        corpus_tarball_path=tarball_path,
        runner_factory=lambda endpoint, secret: endpoint,
        probe_run_fn=lambda runner: iter(()),
    )
    app = create_app(
        rubric=Rubric.load(RUBRIC_PATH),
        bundles=BundleStore(),
        scan_runtime=runtime,
    )
    return TestClient(app)


def test_scan_route_rejects_forged_corpus_version_before_signed_envelope(
    tmp_path,
    monkeypatch,
):
    """Caller corpus_version must match server corpus manifest."""
    monkeypatch.setattr(
        "api.scan_handlers._verify_live_egress_fail_closed",
        lambda **_kwargs: None,
    )
    tarball_path = _corpus_tarball(tmp_path)
    body = _body(compute_corpus_snapshot_hash(tarball_path))
    body["corpus_version"] = "0.1.0-forged"

    response = _client_for_corpus(tarball_path).post(
        "/scanner/v1/scans",
        json=body,
        headers={"X-Tenant-Id": TENANT},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "corpus_version_mismatch"


def test_scan_route_rejects_forged_seed_before_signed_envelope(
    tmp_path,
    monkeypatch,
):
    """Caller seed must match server corpus manifest."""
    monkeypatch.setattr(
        "api.scan_handlers._verify_live_egress_fail_closed",
        lambda **_kwargs: None,
    )
    tarball_path = _corpus_tarball(tmp_path)
    body = _body(compute_corpus_snapshot_hash(tarball_path))
    body["seed"] = 999

    response = _client_for_corpus(tarball_path).post(
        "/scanner/v1/scans",
        json=body,
        headers={"X-Tenant-Id": TENANT},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "seed_mismatch"
