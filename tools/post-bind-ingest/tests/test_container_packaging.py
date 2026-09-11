from __future__ import annotations

from pathlib import Path

import yaml

from post_bind_ingest import cli


ROOT = Path(__file__).resolve().parents[1]


def test_local_compose_disables_network_and_egress_by_default() -> None:
    compose = (ROOT / "docker-compose.local.yml").read_text(encoding="utf-8")

    assert 'network_mode: "none"' in compose
    assert 'BACKBOND_EGRESS_ENABLED: "false"' in compose
    assert "/input:ro" in compose
    assert "/output:rw" in compose
    assert "--enable-egress" not in compose
    assert "--egress-url" not in compose


def test_local_compose_command_is_accepted_by_receiver_cli(monkeypatch, tmp_path: Path) -> None:
    """Run the committed argv through the real parser, not a duplicated command."""
    compose = yaml.safe_load((ROOT / "docker-compose.local.yml").read_text(encoding="utf-8"))
    command = compose["services"]["backbond-monitor"]["command"]
    payload = tmp_path / "receiver_payload.json"
    payload.write_text("{}", encoding="utf-8")
    output = tmp_path / "normalized-events.jsonl"
    argv = [
        str(payload) if value == "/input/receiver_payload.json" else
        '{"fixture-key":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}'
        if value.startswith("${BACKBOND_MONITOR_JWT_KEYRING_JSON:") else
        str(output) if value == "/output/normalized-events.jsonl" else value
        for value in command
    ]
    monkeypatch.setattr("sys.argv", ["post-bind-ingest", *argv])

    # The empty fixture is rejected by receiver validation. Reaching that validation
    # proves every committed compose argument is recognized by the real CLI.
    try:
        cli.main()
    except Exception as exc:  # noqa: BLE001 - validation exception type is contract-owned
        assert "unrecognized arguments" not in str(exc)
    else:
        raise AssertionError("empty receiver fixture unexpectedly accepted")


def test_dockerfile_uses_local_entrypoint_without_network_tools() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["post-bind-ingest"]' in dockerfile
    assert "BACKBOND_EGRESS_ENABLED=false" in dockerfile
    assert "curl" not in dockerfile.lower()
    assert "wget" not in dockerfile.lower()
