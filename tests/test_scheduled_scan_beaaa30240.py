from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.scheduled_scan import ScheduleConfig, map_secret_environment


ROOT = Path(__file__).parents[1]
OPS = ROOT / "ops" / "scan-schedule"


def test_secret_mapping_is_exact_and_does_not_use_obsolete_contract():
    names = {
        "ARE_BUNDLE_SIGNING_KEY": "bundle",
        "ARE_EVAL_PROVENANCE_KEY": "provenance",
        "VAPI_API_KEY": "vapi",
        "VAPI_ASSISTANT_ID": "assistant",
        "OPENROUTER_API_KEY": "router",
    }
    assert map_secret_environment(names) == names
    source = (ROOT / "scripts" / "scheduled_scan.py").read_text()
    assert "SCAN_API_TOKEN" not in source
    assert "VAPI_CREDENTIAL" not in source


def test_schedule_config_enforces_founder_caps(monkeypatch, tmp_path):
    values = {
        "BACKBOND_SCAN_TENANT_ID": "fixture",
        "BACKBOND_SCAN_AGENT_ENDPOINT": "https://api.vapi.ai",
        "BACKBOND_SCAN_EVIDENCE_ROOT": str(tmp_path),
        "BACKBOND_SCAN_SCANNER_VERSION": "0.5.0",
        "BACKBOND_SCAN_CORPUS_VERSION": "0.2.0",
        "BACKBOND_SCAN_JUDGE_MODEL": "fixture/judge",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    config = ScheduleConfig.from_environment()
    config.validate()
    assert (config.concurrency, config.rpm, config.retries, config.cost_cap_usd) == (1, 30, 0, 100)


def test_units_have_required_cadence_and_direct_runner():
    vapi_timer = (OPS / "backbond-scan-vapi.timer").read_text()
    zoo_timer = (OPS / "backbond-scan-zoo.timer").read_text()
    assert "OnCalendar=Mon *-*-* 03:00:00 UTC" in vapi_timer
    assert "OnCalendar=*-*-* 03:30:00 UTC" in zoo_timer
    for name in ("backbond-scan-vapi.service", "backbond-scan-zoo.service"):
        text = (OPS / name).read_text()
        assert "User=are-deploy" in text
        assert "EnvironmentFile=/etc/backbond/scan-schedule.env" in text
        assert "scripts/scheduled_scan.py" in text
        assert "git -C /home/are-deploy/are-scanner" in text
        assert "sudo" not in text
        assert "scanner/v1/scans" not in text


def test_installer_owns_checkout_manifest_and_exact_unit_permissions():
    text = (OPS / "install.sh").read_text()
    assert "su -s /bin/sh -c" in text
    assert "git -C '$checkout'" in text
    assert 'chown "$unit_user:$unit_user" "$manifest_root"' in text
    assert "sudoers.d/backbond-scan-schedule" in text
    assert "backbond-scan-schedule-update" not in text
    for action in ("start", "stop", "restart", "status"):
        assert f"systemctl {action} backbond-scan-vapi.service" in text
        assert f"systemctl {action} backbond-scan-zoo.service" in text
    assert "systemctl enable backbond-scan-vapi.timer backbond-scan-zoo.timer" in text


def test_direct_executor_and_egress_preflight_are_in_source():
    text = (ROOT / "scripts" / "scheduled_scan.py").read_text()
    assert "dispatch_registry_attempt(" in text
    assert "verify_live_egress_fail_closed(" in text
    assert "egress_preflight_result_path" in text
