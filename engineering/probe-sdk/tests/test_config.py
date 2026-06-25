"""Config loader: required fields enforced; telemetry hard-rejected."""
import os
from pathlib import Path

import pytest
import yaml

from are_probe.config import ConfigError, load_config

SDK_ROOT = Path(__file__).resolve().parents[1]


def _example_dict():
    return yaml.safe_load(
        (SDK_ROOT / "config" / "are-probe.yaml.example").read_text()
    )


def _write(tmp_path: Path, d: dict) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(d))
    return p


def test_example_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("ARE_SCAN_BIN", "/usr/bin/true")
    cfg = load_config(_write(tmp_path, _example_dict()))
    assert cfg.probe_sdk_release == "R2-internal"
    assert cfg.seed == 7
    assert cfg.target.shape == "generic_prompt_output"
    assert cfg.target.auth_mode == "none"
    assert cfg.scanner_version == "0.5.0"
    assert cfg.telemetry_enabled is False


def test_telemetry_enabled_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("ARE_SCAN_BIN", "/usr/bin/true")
    d = _example_dict()
    d["telemetry"] = {"enabled": True}
    with pytest.raises(ConfigError) as exc:
        load_config(_write(tmp_path, d))
    assert "telemetry" in str(exc.value).lower()
    assert "freeze" in str(exc.value).lower()


def test_missing_scanner_bin_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("ARE_SCAN_BIN", raising=False)
    d = _example_dict()
    d.pop("scanner_bin_default", None)
    with pytest.raises(ConfigError) as exc:
        load_config(_write(tmp_path, d))
    assert "scanner bin" in str(exc.value).lower()


def test_empty_probes_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("ARE_SCAN_BIN", "/usr/bin/true")
    d = _example_dict()
    d["probes"] = []
    with pytest.raises(ConfigError) as exc:
        load_config(_write(tmp_path, d))
    assert "probes" in str(exc.value).lower()


def test_unknown_auth_mode_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("ARE_SCAN_BIN", "/usr/bin/true")
    d = _example_dict()
    d["target"]["auth"]["mode"] = "oauth2"
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, d))


def test_unknown_target_shape_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("ARE_SCAN_BIN", "/usr/bin/true")
    d = _example_dict()
    d["target"]["shape"] = "browser_automation"
    with pytest.raises(ConfigError) as exc:
        load_config(_write(tmp_path, d))
    assert "target.shape" in str(exc.value)


def test_response_json_paths_must_be_strings(tmp_path, monkeypatch):
    monkeypatch.setenv("ARE_SCAN_BIN", "/usr/bin/true")
    d = _example_dict()
    d["target"]["response_json_paths"] = ["messages.0.text", 7]
    with pytest.raises(ConfigError) as exc:
        load_config(_write(tmp_path, d))
    assert "response_json_paths" in str(exc.value)
