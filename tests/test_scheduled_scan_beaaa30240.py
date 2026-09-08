from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.bundle import sign_manifest
from api.full_scan_executor import FullScanJob, build_attempt_manifest

from scripts.scheduled_scan import (
    EVIDENCE_LABEL,
    RateLimitedRunner,
    ScheduleConfig,
    SharedRateLimiter,
    _operator_manifest,
    map_secret_environment,
    run_positive_egress_preflight,
    run_scheduled_scan,
)


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
        assert "User=backbond-scan" in text
        assert "EnvironmentFile=/etc/backbond/scan-schedule.env" in text
        assert "scripts/scheduled_scan.py" in text
        assert "ExecStartPre=+/usr/bin/env HOME=/root /usr/bin/git -C /opt/backbond/src" in text
        assert "ExecStart=/opt/backbond/scan-venv/bin/python" in text
        assert "sudo" not in text
        assert "scanner/v1/scans" not in text


def test_installer_owns_checkout_manifest_and_exact_unit_permissions():
    text = (OPS / "install.sh").read_text()
    assert "useradd --system --no-create-home --shell /usr/sbin/nologin" in text
    assert "checkout=/opt/backbond/src" in text
    assert "scan_venv=/opt/backbond/scan-venv" in text
    assert 'python3.11 -m venv --clear "$scan_venv"' in text
    assert 'pip install --require-hashes --disable-pip-version-check' in text
    assert "chmod o+x /var/lib/backbond" in text
    assert 'chown "$unit_user:$unit_user" "$manifest_root"' in text
    assert "sudoers.d/backbond-scan-schedule" in text
    assert '"beai-agent ALL=(root) NOPASSWD:' in text
    assert '"$unit_user ALL=(root) NOPASSWD:' not in text
    assert "backbond-scan-schedule-update" not in text
    for action in ("start", "stop", "restart", "status"):
        assert f"systemctl {action} backbond-scan-vapi.service" in text
        assert f"systemctl {action} backbond-scan-zoo.service" in text
    assert "systemctl enable backbond-scan-vapi.timer" in text
    assert "systemctl disable backbond-scan-zoo.timer" in text


def test_zoo_job_refuses_before_secrets_or_vapi_worker_construction(monkeypatch):
    monkeypatch.setattr(
        "scripts.scheduled_scan.map_secret_environment",
        lambda: pytest.fail("zoo must not read Vapi secrets"),
    )
    monkeypatch.setattr(
        "scripts.scheduled_scan.build_registered_case_workers",
        lambda *args, **kwargs: pytest.fail("zoo must not construct Vapi workers"),
    )
    with pytest.raises(RuntimeError, match="controlled-target runner"):
        run_scheduled_scan("zoo")


def test_direct_executor_and_egress_preflight_are_in_source():
    text = (ROOT / "scripts" / "scheduled_scan.py").read_text()
    assert "dispatch_registry_attempt(" in text
    assert "run_positive_egress_preflight(" in text
    assert "egress_preflight_result_path" in text


def test_target_and_judge_share_one_aggregate_30_rpm_limiter():
    now = [0.0]
    calls: list[tuple[str, float]] = []

    def sleep(seconds):
        now[0] += seconds

    class Runner:
        def __init__(self, name):
            self.name = name

        def model_id(self):
            return self.name

        def complete(self, prompt):
            calls.append((self.name, now[0]))
            return prompt

    limiter = SharedRateLimiter(30, clock=lambda: now[0], sleep=sleep)
    target = RateLimitedRunner(Runner("target"), limiter)
    judge = RateLimitedRunner(Runner("judge"), limiter)
    target.complete("one")
    judge.complete("two")
    target.complete("three")
    assert calls == [("target", 0.0), ("judge", 2.0), ("target", 4.0)]


def test_positive_preflight_records_exact_hosts_and_http_codes(tmp_path):
    codes = {"github.com": 200, "api.vapi.ai": 401, "openrouter.ai": 302}

    def request(url, **kwargs):
        host = url.split("//", 1)[1].rstrip("/")
        assert kwargs == {"timeout": 10.0, "follow_redirects": False}
        return SimpleNamespace(status_code=codes[host])

    path = tmp_path / "preflight.json"
    result = run_positive_egress_preflight(path, requester=request)
    assert result["credential_free"] is True
    assert result["passed"] is True
    assert {host: row["http_status_code"] for host, row in result["endpoints"].items()} == codes


@pytest.mark.parametrize("error", [ModuleNotFoundError("httpx"), ImportError("httpx")])
def test_positive_preflight_classifies_missing_runtime_dependency(tmp_path, error):
    path = tmp_path / "preflight.json"

    def request(*args, **kwargs):
        raise error

    with pytest.raises(RuntimeError, match="preflight dependency missing"):
        run_positive_egress_preflight(path, requester=request)

    record = json.loads(path.read_text())
    assert record["passed"] is False
    assert {
        row["error_type"] for row in record["endpoints"].values()
    } == {"dependency_missing"}


def test_positive_preflight_keeps_transport_failure_distinct(tmp_path):
    path = tmp_path / "preflight.json"

    def request(*args, **kwargs):
        raise TimeoutError("timed out")

    with pytest.raises(RuntimeError, match="scheduled egress preflight failed"):
        run_positive_egress_preflight(path, requester=request)

    record = json.loads(path.read_text())
    assert {row["error_type"] for row in record["endpoints"].values()} == {"TimeoutError"}


def test_manifest_has_exact_operator_counts_cost_runtime_and_evidence_class(tmp_path):
    config = ScheduleConfig(
        "tenant", "endpoint", tmp_path, "scanner", "corpus", 0, 1, 30, 0, 100, 30, "judge"
    )
    rows = [
        {"test_id": "safety", "status": "complete", "verdict": "PASS", "target_calls": 1},
        {"test_id": "safety", "status": "complete", "verdict": "FAIL", "target_calls": 1},
        {
            "test_id": "tools",
            "status": "incomplete",
            "incomplete_reason": "worker_timeout:x",
            "target_calls": 0,
        },
        {
            "test_id": "tools",
            "status": "incomplete",
            "incomplete_reason": "missing_judge",
            "target_calls": 1,
        },
        {
            "test_id": "tools",
            "status": "incomplete",
            "incomplete_reason": "worker_unavailable:x",
            "target_calls": 0,
        },
        {
            "test_id": "tools",
            "status": "incomplete",
            "incomplete_reason": "invalid_response",
            "target_calls": 1,
        },
    ]
    registry_rows = (
        {
            "test_id": "safety",
            "status": "active",
            "production_runner_executable": True,
        },
        {
            "test_id": "safety",
            "status": "active",
            "production_runner_executable": True,
        },
        {
            "test_id": "safety",
            "status": "active",
            "production_runner_executable": False,
        },
        *(
            {
                "test_id": "tools",
                "status": "active",
                "production_runner_executable": True,
            }
            for _ in range(4)
        ),
    )
    job = SimpleNamespace(
        job_id="j1",
        scan_id="s1",
        status="complete",
        bind_eligibility="blocked",
        final_manifest_hash="sha256:x",
        final_manifest={
            "evidence_rows": rows,
            "budget": {"estimated_judge_cost_usd": 12.25},
        },
    )
    manifest = _operator_manifest(
        job_name="vapi",
        config=config,
        job=job,
        preflight_path=tmp_path / "preflight.json",
        started_at="start",
        finished_at="finish",
        total_runtime_seconds=9.8765,
        registry_rows=registry_rows,
        preflight_result={"passed": True, "endpoints": {"github.com": {"http_status_code": 200}}},
    )
    assert manifest["evidence_label"] == EVIDENCE_LABEL
    assert manifest["live_service_signed"] is False
    assert manifest["total_runtime_seconds"] == 9.877
    assert manifest["estimated_cost_usd"] == 12.25
    assert manifest["egress_preflight"]["passed"] is True
    assert manifest["families"]["safety"] == {
        "considered": 3,
        "routed": 2,
        "target_called": 2,
        "pass": 1,
        "fail": 1,
        "incomplete": 0,
    }
    assert manifest["incomplete_by_reason"] == {
        "dispatch_failure": 2,
        "transport": 0,
        "format": 1,
        "missing_judge": 1,
        "unavailable_capability": 0,
    }


def test_signed_attempt_manifest_binds_scheduled_evidence_class(monkeypatch):
    monkeypatch.setenv("ARE_ALLOW_DEV_SIGNING_KEY", "true")
    job = FullScanJob(
        job_id="scheduled-job",
        agent_id="agent",
        scanner_version="scanner",
        corpus_version="corpus",
        model_versions={"evidence_class": EVIDENCE_LABEL},
        seed=0,
        registry_cases=(),
    )
    manifest = build_attempt_manifest(job)
    unsigned = {
        key: value
        for key, value in manifest.items()
        if key not in {"signed_manifest", "manifest_hash"}
    }
    assert unsigned["evidence_class"] == EVIDENCE_LABEL
    assert unsigned["live_service_signed"] is False
    assert "live-service-signed" not in str(unsigned)
    assert manifest["signed_manifest"] == sign_manifest(unsigned)
