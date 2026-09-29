from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_installer_restarts_both_units_and_records_runtime_version():
    text = (ROOT / "ops/stage-b-inbound/install.sh").read_text()
    assert "systemctl restart backbond-stage-b-inbound.service" in text
    assert "systemctl restart backbond-stage-b-receipts.service" in text
    assert ".backbond-stage-b-version" in text
    assert "stage_b_runtime_version=" in text
    assert "runtime_backup" in text and "rollback" in text


def test_receipt_service_has_its_own_protected_secret_file():
    unit = (ROOT / "ops/stage-b-inbound/backbond-stage-b-receipts.service").read_text()
    installer = (ROOT / "ops/stage-b-inbound/install.sh").read_text()
    assert "EnvironmentFile=/etc/backbond/stage-b-receipts.env" in unit
    assert "BB_DEMO_SESSION_SECRET=" in installer
    assert 'chmod 0600 "$receipt_secret_file"' in installer
    assert "stage-b-inbound.env" not in unit
