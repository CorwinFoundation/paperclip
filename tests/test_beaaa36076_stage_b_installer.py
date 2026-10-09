from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_installer_restarts_both_units_and_records_runtime_version():
    text = (ROOT / "ops/stage-b-inbound/install.sh").read_text()
    assert "systemctl restart backbond-stage-b-inbound.service" in text
    assert "systemctl restart backbond-stage-b-receipts.service" in text
    assert ".backbond-stage-b-version" in text
    assert "stage_b_runtime_version=" in text
    assert "runtime_backup" in text and "rollback" in text


def test_transport_probe_arming_imports_installed_package_as_service_user_from_unrelated_cwd():
    text = (ROOT / "ops/stage-b-inbound/install.sh").read_text()
    assert 'PYTHONPATH="$repo_root"' not in text
    assert 'runuser -u backbond-stage-b' in text
    assert "cd / && env PYTHONPATH=\"$1\"" in text
    assert 'sh "$app_dir" "$state_dir/history.sqlite3"' in text
    assert "probe_preflight_db=$state_dir/transport-probe-preflight.sqlite3" in text
    assert "print(s.issue_transport_probe())" in text
    assert text.index("probe_preflight_db=") < text.rindex(
        "systemctl restart backbond-stage-b-inbound.service"
    )


def test_installer_protects_mutable_state_and_credentials_for_rollback():
    text = (ROOT / "ops/stage-b-inbound/install.sh").read_text()
    assert 'chmod 0700 "$runtime_backup"' in text
    assert 'systemctl stop backbond-stage-b-receipts.service backbond-stage-b-inbound.service' in text
    assert 'cp -a "$state_dir" "$runtime_backup/state"' in text
    assert 'cp -a "$runtime_backup/state" "$state_dir"' in text
    assert 'cp -a "$secret_file" "$runtime_backup/stage-b-inbound.env"' in text
    assert 'cp -a "$receipt_secret_file" "$runtime_backup/stage-b-receipts.env"' in text


def test_receipt_service_has_its_own_protected_secret_file():
    unit = (ROOT / "ops/stage-b-inbound/backbond-stage-b-receipts.service").read_text()
    installer = (ROOT / "ops/stage-b-inbound/install.sh").read_text()
    assert "EnvironmentFile=/etc/backbond/stage-b-receipts.env" in unit
    assert "BB_DEMO_SESSION_SECRET=" in installer
    assert 'chmod 0600 "$receipt_secret_file"' in installer
    assert "stage-b-inbound.env" not in unit


def test_receipt_service_can_persist_isolated_onboarding_sessions():
    unit = (ROOT / "ops/stage-b-inbound/backbond-stage-b-receipts.service").read_text()
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/var/lib/backbond-stage-b" in unit


def test_installer_places_redacted_instinct_acceptance_capture_helper():
    installer = (ROOT / "ops/stage-b-inbound/install.sh").read_text()
    assert "capture-instinct-acceptance.py" in installer
    assert "/usr/local/sbin/backbond-stage-b-capture-instinct-acceptance" in installer


def test_installer_has_explicit_idempotent_real_history_activation_switch():
    installer = (ROOT / "ops/stage-b-inbound/install.sh").read_text()
    assert '${BB_ENABLE_REAL_HISTORY_ON_INSTALL:-0}' in installer
    assert "BB_HISTORY_EXTERNAL_ENABLED=1" in installer
