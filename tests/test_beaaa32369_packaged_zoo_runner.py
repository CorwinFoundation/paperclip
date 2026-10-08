"""BEAAA-32369: the automatic scanner image carries an on-box zoo command."""
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def test_image_packages_zoo_runtime_configuration() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "BACKBOND_SCAN_EVIDENCE_ROOT=/var/lib/are-scanner/scheduled-scans" in dockerfile
    assert "BACKBOND_SCAN_SCANNER_VERSION=0.5.0" in dockerfile
    assert "BACKBOND_SCAN_CORPUS_VERSION=0.2.0" in dockerfile
    assert "COPY --from=build /app /app" in dockerfile


def test_on_box_route_runs_packaged_runner_and_returns_manifest(monkeypatch, tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        '{"families":{"f":{"considered":1,"routed":1,"target_called":1,'
        '"pass":1,"fail":0,"incomplete":0}},"incomplete_by_reason":{},'
        '"total_runtime_seconds":1.5,"estimated_cost_usd":0.01}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.scheduled_scan.run_scheduled_zoo_scan", lambda **_: manifest_path
    )
    client = TestClient(create_app())

    response = client.post("/internal/ops/controlled-zoo/run")

    assert response.status_code == 200
    body = response.json()
    assert body["manifest_path"] == str(manifest_path)
    assert body["manifest"]["families"]["f"]["target_called"] == 1
    assert body["manifest"]["total_runtime_seconds"] == 1.5
    assert body["manifest"]["estimated_cost_usd"] == 0.01


def test_on_box_route_forwards_exact_zoo_selector(monkeypatch, tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"families":{}}', encoding="utf-8")
    captured = {}

    def run(**kwargs):
        captured.update(kwargs)
        return manifest_path

    monkeypatch.setattr("scripts.scheduled_scan.run_scheduled_zoo_scan", run)
    client = TestClient(create_app())

    response = client.post(
        "/internal/ops/controlled-zoo/run",
        params={"zoo_family": "family-a", "zoo_case": "case-1"},
    )

    assert response.status_code == 200
    assert captured == {"zoo_family": "family-a", "zoo_case": "case-1"}


def test_on_box_route_rejects_partial_zoo_selector() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/internal/ops/controlled-zoo/run", params={"zoo_family": "family-a"}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "zoo_selector_requires_family_and_case"


def test_on_box_route_rejects_forwarded_requests() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/internal/ops/controlled-zoo/run", headers={"X-Forwarded-For": "203.0.113.5"}
    )
    assert response.status_code == 404
