from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "infra/langfuse-backup/docker-compose.yml"


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


def test_sidecar_is_score_neutral_and_n5_complete():
    receipt = json.loads(
        (ROOT / "tools/calibration-deltas/2026-07-24-beaaa17701-langfuse-sidecar-n5.json").read_text()
    )
    assert receipt["calibration_delta"] == [0, 0, 0, 0, 0]
    assert receipt["max_absolute_band_delta"] == 0
    assert receipt["rubric_point_id"] == "N/A_OBSERVABILITY_SIDECAR"
