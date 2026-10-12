from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/deploy/withhold-scan-report.sh"
TOKEN = "99c83595be4fd9087cc85ba8790c37fc"


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def test_withhold_is_ordered_idempotent_and_reloads(tmp_path: Path) -> None:
    config = tmp_path / "Caddyfile"
    config.write_text("api.backbond.ai {\n    @scan_intake path /scan/v1/*\n    handle @scan_intake {\n    }\n}\n")
    calls = tmp_path / "calls"
    caddy = tmp_path / "caddy"
    systemctl = tmp_path / "systemctl"
    _exe(caddy, f'#!/bin/sh\necho "caddy $*" >>"{calls}"\n')
    _exe(systemctl, f'#!/bin/sh\necho "systemctl $*" >>"{calls}"\n')
    env = {**os.environ, "CADDYFILE": str(config), "CADDY": str(caddy), "SYSTEMCTL": str(systemctl)}

    first = subprocess.run(["bash", str(SCRIPT), TOKEN], env=env, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    text = config.read_text()
    assert text.index(f"@withheld_report_{TOKEN}") < text.index("@scan_intake")
    assert "respond 404" in text
    assert calls.read_text().splitlines() == [
        f"caddy validate --config {config}",
        "systemctl reload caddy",
    ]

    second = subprocess.run(["bash", str(SCRIPT), TOKEN], env=env, text=True, capture_output=True)
    assert second.returncode == 0
    assert config.read_text() == text
    assert config.read_text().count(f"@withheld_report_{TOKEN}") == 2  # matcher + handle
    assert len(list(tmp_path.glob("Caddyfile.before-withhold-*"))) == 1


def test_validation_failure_restores_original(tmp_path: Path) -> None:
    config = tmp_path / "Caddyfile"
    original = "api.backbond.ai {\n    @scan_intake path /scan/v1/*\n}\n"
    config.write_text(original)
    caddy = tmp_path / "caddy"
    systemctl = tmp_path / "systemctl"
    _exe(caddy, '#!/bin/sh\ncase "$*" in *validate*) exit 1;; esac\n')
    _exe(systemctl, "#!/bin/sh\nexit 0\n")
    env = {**os.environ, "CADDYFILE": str(config), "CADDY": str(caddy), "SYSTEMCTL": str(systemctl)}

    result = subprocess.run(["bash", str(SCRIPT), TOKEN], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert config.read_text() == original
