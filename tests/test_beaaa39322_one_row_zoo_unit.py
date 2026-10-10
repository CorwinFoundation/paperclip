"""BEAAA-39322: expose exactly one fresh Zoo row through the operator grant."""

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "ops" / "scan-schedule"
UNIT_NAME = "backbond-scan-zoo-beaaa39322.service"


def test_diagnostic_unit_is_exactly_one_row_and_has_no_timer():
    unit = (OPS / UNIT_NAME).read_text(encoding="utf-8")
    assert "--job zoo" in unit
    assert "--zoo-family advbench" in unit
    assert "--zoo-case advbench.b2b_harmful" in unit
    assert not (OPS / "backbond-scan-zoo-beaaa39322.timer").exists()


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
    grant = (sudoers_dir / "backbond-scan-beaaa39322").read_text(encoding="utf-8")
    assert f"systemctl start {UNIT_NAME}" in grant
    assert f"systemctl status {UNIT_NAME}" in grant
    assert "systemctl stop" not in grant
    assert "systemctl restart" not in grant
    assert "backbond-scan-zoo.service" not in grant
