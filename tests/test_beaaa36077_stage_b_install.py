from pathlib import Path


ROOT = Path(__file__).parents[1]
INSTALLER = (ROOT / "ops/stage-b-inbound/install.sh").read_text()
RECEIPT_UNIT = (ROOT / "ops/stage-b-inbound/backbond-stage-b-receipts.service").read_text()
NGINX = (ROOT / "ops/stage-b-inbound/nginx-location.conf").read_text()


def test_receipt_service_has_dedicated_protected_runtime_config():
    assert "EnvironmentFile=/etc/backbond/stage-b-receipts.env" in RECEIPT_UNIT
    assert "stage-b-inbound.env" not in RECEIPT_UNIT
    assert "BB_DEMO_SESSION_SECRET" in INSTALLER
    assert "BB_DEMO_CREDENTIAL_FILE" in INSTALLER
    assert "receipt_session_secret_missing" in INSTALLER
    assert 'chmod 0600 "$receipt_secret_file"' in INSTALLER


def test_installer_restarts_changed_services_and_records_version_evidence():
    assert "systemctl restart backbond-stage-b-inbound.service" in INSTALLER
    assert "systemctl restart backbond-stage-b-receipts.service" in INSTALLER
    assert "systemctl enable --now" not in INSTALLER
    assert "DEPLOYED_COMMIT" in INSTALLER
    assert "SOURCE_COMMIT" in INSTALLER
    assert "candidate_commit_missing_or_invalid" in INSTALLER
    assert INSTALLER.index("candidate_commit=$(git") < INSTALLER.index("systemctl stop")


def test_loopback_readiness_waits_for_restarted_services() -> None:
    assert INSTALLER.count("--retry 10 --retry-delay 1 --retry-connrefused") == 3
    assert "http://127.0.0.1:8788/healthz" in INSTALLER
    assert "http://127.0.0.1:8789/stage-b-demo/receipts" in INSTALLER
    assert "http://127.0.0.1:8789/stage-b-demo/history" in INSTALLER
    assert "stage_b_candidate=" in INSTALLER
    assert "inbound_main_pid=" in INSTALLER
    assert "receipts_main_pid=" in INSTALLER


def test_installer_preserves_application_and_units_for_rollback():
    assert 'cp -a "$app_dir" "$runtime_backup/app"' in INSTALLER
    assert 'cp -a "$runtime_backup/app" "$app_dir"' in INSTALLER
    assert 'cp -a "$runtime_backup/inbound.service" "$unit_file"' in INSTALLER
    assert 'cp -a "$runtime_backup/receipts.service" "$receipt_unit_file"' in INSTALLER
    assert "systemctl restart backbond-stage-b-inbound.service backbond-stage-b-receipts.service" in INSTALLER


def test_public_receipt_and_logout_routes_reach_isolated_service_without_answer_relay():
    assert "location = /stage-b-demo/receipts" in NGINX
    assert "location = /stage-b-demo/logout" in NGINX
    assert "http://127.0.0.1:8789/stage-b-demo/logout" in NGINX
    assert "stage-b-demo/answer" not in NGINX
