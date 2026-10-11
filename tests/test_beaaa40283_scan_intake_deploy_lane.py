from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/deploy/scanner-deploy.sh"
UPDATE = ROOT / "backbond/scan-intake/update-server.sh"
WORKFLOW = ROOT / ".github/workflows/scanner-edge-image.yml"
SHA = "a" * 40


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def test_scanner_master_lane_does_not_target_scan_intake_host() -> None:
    text = WORKFLOW.read_text()
    assert "Deploy scan-intake from exact live revision" not in text
    assert "sudo '${DEPLOY_HELPER}' scan-intake '${SHA}'" not in text


def test_update_server_bootstraps_the_single_authorized_helper() -> None:
    text = UPDATE.read_text()
    assert 'scripts/deploy/scanner-deploy.sh' in text
    assert '/usr/local/sbin/scanner-deploy' in text
    assert 'SERVICE="${SCAN_INTAKE_SERVICE:-backbond-scan-intake.service}"' in text


def test_scan_intake_subcommand_requires_exact_running_revision(tmp_path: Path) -> None:
    docker = tmp_path / "docker"
    _exe(docker, "#!/bin/sh\n[ \"$1\" = inspect ] && printf '%s\\n' bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n")
    result = subprocess.run(
        ["bash", str(HELPER), "scan-intake", SHA],
        env={**os.environ, "SCANNER_DOCKER": str(docker)},
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert f"live revision mismatch: expected {SHA}" in result.stderr


def test_scan_intake_subcommand_copies_payload_and_runs_updater(tmp_path: Path) -> None:
    docker = tmp_path / "docker"
    systemctl = tmp_path / "systemctl"
    calls = tmp_path / "calls"
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "update-server.sh").write_text(
        f"#!/bin/sh\nprintf 'service=%s\\n' \"$SCAN_INTAKE_SERVICE\" > {calls}\n"
    )
    _exe(
        docker,
        f'''#!/bin/sh
if [ "$1" = inspect ]; then printf '%s\\n' {SHA}; exit 0; fi
if [ "$1" = cp ]; then cp -a {payload}/. "$3"; exit 0; fi
exit 2
''',
    )
    _exe(systemctl, "#!/bin/sh\nexit 0\n")
    result = subprocess.run(
        ["bash", str(HELPER), "scan-intake", SHA],
        env={
            **os.environ,
            "SCANNER_DOCKER": str(docker),
            "SCANNER_SYSTEMCTL": str(systemctl),
            "SCANNER_SCAN_INTAKE_SERVICE": "test-scan-intake.service",
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert calls.read_text() == "service=test-scan-intake.service\n"
    assert f"OK: scan-intake deployed from {SHA}" in result.stderr
