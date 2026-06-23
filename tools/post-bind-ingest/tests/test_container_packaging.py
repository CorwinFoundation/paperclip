from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_local_compose_disables_network_and_egress_by_default() -> None:
    compose = (ROOT / "docker-compose.local.yml").read_text(encoding="utf-8")

    assert 'network_mode: "none"' in compose
    assert 'BACKBOND_EGRESS_ENABLED: "false"' in compose
    assert "/input:ro" in compose
    assert "/output:rw" in compose
    assert "--enable-egress" not in compose
    assert "--egress-url" not in compose


def test_dockerfile_uses_local_entrypoint_without_network_tools() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["post-bind-ingest"]' in dockerfile
    assert "BACKBOND_EGRESS_ENABLED=false" in dockerfile
    assert "curl" not in dockerfile.lower()
    assert "wget" not in dockerfile.lower()
