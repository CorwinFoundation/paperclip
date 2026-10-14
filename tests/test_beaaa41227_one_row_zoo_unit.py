"""BEAAA-41227: install one bounded sealed Zoo row for oosc-001."""

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "ops" / "scan-schedule"
UNIT_NAME = "backbond-scan-zoo-beaaa41227.service"


def test_unit_preserves_sealed_contract_and_selects_exactly_one_row():
    unit = (OPS / UNIT_NAME).read_text(encoding="utf-8")
    source = (OPS / "backbond-scan-zoo.service").read_text(encoding="utf-8")
    for line in (
        "User=backbond-scan",
        "EnvironmentFile=/etc/backbond/scan-schedule.env",
        "EnvironmentFile=/etc/backbond/scan-schedule.conf",
        "WorkingDirectory=/opt/backbond/src",
    ):
        assert line in unit
        assert line in source
    assert "git -C /opt/backbond/src fetch --quiet origin master" in unit
    assert "git -C /opt/backbond/src merge --ff-only --quiet origin/master" in unit
    assert "TimeoutStartSec=21600" in unit
    assert (
        "ExecStart=/opt/backbond/scan-venv/bin/python scripts/scheduled_scan.py "
        "--job zoo --zoo-family out_of_scope_containment --zoo-case oosc-001"
    ) in unit
    assert not (OPS / "backbond-scan-zoo-beaaa41227.timer").exists()


def test_narrow_installer_grants_only_start_and_status(tmp_path):
    unit_dir = tmp_path / "systemd"
    sudoers_dir = tmp_path / "sudoers"
    unit_dir.mkdir()
    sudoers_dir.mkdir()
    result = subprocess.run(
        ["sh", str(OPS / "install-units-only.sh"), str(unit_dir), str(sudoers_dir)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (unit_dir / UNIT_NAME).is_file()
    grant = (sudoers_dir / "backbond-scan-beaaa41227").read_text(encoding="utf-8")
    assert f"systemctl start {UNIT_NAME}" in grant
    assert f"systemctl status {UNIT_NAME}" in grant
    assert "systemctl stop" not in grant
    assert "systemctl restart" not in grant
    assert "backbond-scan-zoo.service" not in grant
