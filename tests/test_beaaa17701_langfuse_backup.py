from pathlib import Path
import importlib.util
import json
import re
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "infra/langfuse-backup/docker-compose.yml"
ACCEPTANCE = ROOT / "infra/langfuse-backup/synthetic_acceptance.py"


def load_acceptance():
    spec = importlib.util.spec_from_file_location("beaaa17701_synthetic_acceptance", ACCEPTANCE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_every_image_is_digest_pinned():
    text = COMPOSE.read_text()
    images = re.findall(r"^\s+image:\s+(\S+)$", text, re.MULTILINE)
    assert len(images) == 6
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", image) for image in images)


def test_only_web_publishes_loopback_port():
    text = COMPOSE.read_text()
    assert text.count("ports:") == 1
    assert '127.0.0.1:${LANGFUSE_LOOPBACK_PORT:-3300}:3000' in text
    assert "internal: true" in text


def test_minio_healthcheck_uses_ipv4_loopback():
    text = COMPOSE.read_text()
    assert 'test: ["CMD", "mc", "ready", "http://127.0.0.1:9000"]' in text
    assert 'test: ["CMD", "mc", "ready", "local"]' not in text


def test_sidecar_is_score_neutral_and_n5_complete():
    receipt = json.loads(
        (ROOT / "tools/calibration-deltas/2026-07-24-beaaa17701-langfuse-sidecar-n5.json").read_text()
    )
    assert receipt["calibration_delta"] == [0, 0, 0, 0, 0]
    assert receipt["max_absolute_band_delta"] == 0
    assert receipt["rubric_point_id"] == "N/A_OBSERVABILITY_SIDECAR"


def test_acceptance_retries_bounded_startup_transport_failure(monkeypatch):
    acceptance = load_acceptance()
    attempts = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return b"{}"

    def urlopen(*_, **__):
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionResetError("migration startup race")
        if len(attempts) == 2:
            raise urllib.error.URLError("connection refused")
        return Response()

    monkeypatch.setenv("LANGFUSE_ENDPOINT", "http://127.0.0.1:3300")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setattr(acceptance.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(acceptance.time, "sleep", lambda _: None)

    status, body = acceptance.request(
        "POST",
        "/api/public/ingestion",
        {},
        transport_attempts=3,
    )

    assert status == 200
    assert body == {}
    assert len(attempts) == 3
