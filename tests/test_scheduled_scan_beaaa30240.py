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
    _ensure_writable_worker_home,
    _operator_manifest,
    map_secret_environment,
    load_garak_dispatch_rows,
    _rows_for_job,
    _worker_factory_for_job,
    run_positive_egress_preflight,
    run_scheduled_scan,
)


def test_garak_live_dispatch_keeps_new_unscored_rows_out():
    rows = load_garak_dispatch_rows()
    assert len(rows) == 27
    assert {row["test_id"] for row in rows} == {"garak", "garak_beaaa152"}
    assert all(row["garak_live_dispatch"] is True for row in rows)
    assert len({row["runtime_case_identity"] for row in rows}) == 27
    assert not {
        "garak.encoding.InjectAscii85",
        "garak.encoding.InjectAtbash",
        "garak.encoding.InjectSneakyBits",
        "garak.ansiescape.AnsiEscaped",
        "garak.dra.DRA",
        "garak.badchars.BadCharacters",
        "garak.smuggling.FunctionMasking",
        "garak.smuggling.HomoglyphObfuscation",
        "garak.apikey.GetKey",
        "garak.web_injection.MarkdownURIImageExfilExtended",
        "garak.web_injection.MarkdownURINonImageExfilExtended",
        "garak.sysprompt_extraction.SystemPromptExtraction",
    } & {row["case_id"] for row in rows}


def test_default_vapi_promotes_garak_into_the_live_worker_union():
    rows, execution_flag = _rows_for_job("vapi")
    garak = [row for row in rows if row.get("test_id") in {"garak", "garak_beaaa152"}]

    assert execution_flag == "production_runner_executable"
    assert len(garak) == 19  # BEAAA-31623 canonical runtime identities
    assert all(row[execution_flag] is True for row in garak)
    assert _worker_factory_for_job("vapi").__name__ == "build_live_framework_workers"


def test_vapi_garak_debug_lane_keeps_native_dispatch_flag_and_factory():
    rows, execution_flag = _rows_for_job("vapi-garak")

    assert len(rows) == 27
    assert execution_flag == "garak_live_dispatch"
    assert all(row[execution_flag] is True for row in rows)
    assert _worker_factory_for_job("vapi-garak").__name__ == "build_live_framework_workers"


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
    names["ANTHROPIC_API_KEY"] = "anthropic"
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
        "BACKBOND_SCAN_JUDGE_PROVIDER": "anthropic",
        "BACKBOND_SCAN_JUDGE_MODEL": "fixture/judge",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    config = ScheduleConfig.from_environment()
    config.validate()
    assert (config.concurrency, config.rpm, config.retries, config.cost_cap_usd) == (1, 30, 0, 100)
    assert config.judge_provider == "anthropic"


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
    assert "systemctl enable backbond-scan-zoo.timer" in text


def test_installer_materializes_isolated_garak_worker_venv():
    install = (OPS / "install.sh").read_text()
    config = (OPS / "scan-schedule.conf").read_text()

    assert "eval_venvs=/opt/are-scanner/eval-venvs" in install
    assert 'ARE_EVAL_VENVS_DIR="$eval_venvs"' in install
    assert 'PYTHON=python3.12' in install
    assert '"$checkout/scripts/build_eval_framework_venvs.sh" garak' in install
    assert "ARE_EVAL_VENVS_DIR=/opt/are-scanner/eval-venvs" in config


def test_zoo_job_never_reads_vapi_secrets(monkeypatch):
    """BEAAA-30515 replaced the refusal stub with a real controlled-target run.

    The property this test was written to hold is unchanged and still the
    point: whatever the zoo lane does, it must not reach for a Vapi
    credential. Only the observable changed — the lane now fails closed on its
    own missing secret instead of refusing outright.
    """
    monkeypatch.setattr(
        "scripts.scheduled_scan.map_secret_environment",
        lambda *args, **kwargs: pytest.fail("zoo must not read Vapi secrets"),
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "x")
    monkeypatch.setenv("ARE_EVAL_PROVENANCE_KEY", "x")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
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
        model_versions={"judge_models": ["anthropic:claude-haiku-4-5-20251001"]},
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
    assert manifest["judge_provider"] == "anthropic"
    assert manifest["judge_models"] == ["anthropic:claude-haiku-4-5-20251001"]
    assert manifest["egress_preflight"]["passed"] is True
    assert manifest["families"]["safety"] == {
        "considered": 3,
        "routed": 2,
        "target_called": 2,
        "pass": 1,
        "fail": 1,
        "measured": 0,
        "incomplete": 0,
        "not_applicable": 0,
    }
    assert manifest["incomplete_by_reason"] == {
        "aborted_upstream": 0,
        "dispatch_failure": 2,
        "transport": 0,
        "format": 0,
        "insufficient_sample": 0,
        "missing_judge": 1,
        "unclassified": 1,
        "unavailable_capability": 0,
        "upstream_http": 0,
    }
    assert manifest["evidence_rows"] == 6
    assert all(row["executed_in_latest_evidenced_run"] for row in manifest["row_results"])
    assert manifest["dimensions"] == {}


def test_never_dispatched_rows_are_not_reported_as_format(tmp_path):
    rows = [
        {
            "test_id": family,
            "status": "incomplete",
            "verdict": "unresolved",
            "incomplete_reason": "case_execution_error:ValueError",
            "target_calls": 0,
        }
        for family, count in {
            "insecure_code_generation": 20,
            "human_escalation_flagging": 20,
            "tool_misuse_unauthorized_call": 12,
            "tool_misuse_scope_escape": 12,
            "mcp_tool_integrity": 12,
            "customer_defined_risk_pack": 12,
        }.items()
        for _ in range(count)
    ]
    families, reasons = __import__(
        "scripts.scheduled_scan", fromlist=["_family_summary"]
    )._family_summary([], rows)

    assert reasons["dispatch_failure"] == 88
    assert reasons["format"] == 0
    assert all(families[name]["target_called"] == 0 for name in families)


def test_terminal_not_applicable_is_not_counted_as_incomplete():
    families, reasons = __import__(
        "scripts.scheduled_scan", fromlist=["_family_summary"]
    )._family_summary([], [{
        "test_id": "mcp_tool_integrity", "status": "complete",
        "verdict": "not_applicable", "target_calls": 0,
    }])

    assert families["mcp_tool_integrity"]["not_applicable"] == 1
    assert families["mcp_tool_integrity"]["incomplete"] == 0
    assert sum(reasons.values()) == 0


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


def test_installer_enables_both_timers_and_never_uses_now():
    """BEAAA-31590: the zoo timer is now enabled; `--now` stays banned.

    BEAAA-30240 held the zoo timer disabled for one stated reason - the
    nightly job was a stub that refused to start, so arming it produced a
    nightly refusal. BEAAA-30515 replaced that stub with a real
    controlled-target run and the 2026-09-10 manifest recorded
    target_called=2234 across 122 families, which is the precondition
    BEAAA-30240 named. The timer is enabled here.

    The other half of BEAAA-30240 is untouched and still load-bearing. PR #386
    collapsed the enable/disable pair into `systemctl enable --now`; both timer
    units carry Persistent=true, so `--now` fires a full scan at install time
    on a host the operator is still provisioning. Enable, never `--now`.
    """
    installer = (
        Path(__file__).resolve().parents[1] / "ops" / "scan-schedule" / "install.sh"
    ).read_text()
    enable_lines = [
        line.strip()
        for line in installer.splitlines()
        if line.strip().startswith("systemctl enable")
    ]
    assert enable_lines == [
        "systemctl enable backbond-scan-vapi.timer",
        "systemctl enable backbond-scan-zoo.timer",
    ]
    assert "--now" not in installer


def test_execution_environment_is_derived_not_hardcoded(monkeypatch):
    """Provenance must name the host that actually captured and judged."""
    from scripts.scheduled_scan import _default_execution_environment

    monkeypatch.setattr("socket.gethostname", lambda: "AriClaw")
    assert _default_execution_environment() == "ariclaw-scheduled-unit"

    monkeypatch.setattr("socket.gethostname", lambda: "someother.host.example")
    assert _default_execution_environment() == "someother-scheduled-unit"

    for name, value in {
        "BACKBOND_SCAN_TENANT_ID": "tenant",
        "BACKBOND_SCAN_AGENT_ENDPOINT": "endpoint",
        "BACKBOND_SCAN_EVIDENCE_ROOT": "/tmp",
        "BACKBOND_SCAN_SCANNER_VERSION": "scanner",
            "BACKBOND_SCAN_CORPUS_VERSION": "corpus",
            "BACKBOND_SCAN_JUDGE_PROVIDER": "anthropic",
            "BACKBOND_SCAN_JUDGE_MODEL": "judge",
        "BACKBOND_SCAN_EXECUTION_ENVIRONMENT": "explicit-host-unit",
    }.items():
        monkeypatch.setenv(name, value)
    assert ScheduleConfig.from_environment().execution_environment == (
        "explicit-host-unit"
    )


def test_worker_home_falls_back_under_evidence_root_when_home_is_unwritable(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", "/nonexistent/backbond-scan")
    evidence_root = tmp_path / "scans"
    evidence_root.mkdir()

    home = _ensure_writable_worker_home(evidence_root)

    assert home == evidence_root / ".worker-home"
    assert home.is_dir()
    assert os.environ["HOME"] == str(home)
    assert os.environ["XDG_CACHE_HOME"] == str(home / "cache")
    assert Path(os.environ["XDG_DATA_HOME"]).is_dir()


def test_worker_home_keeps_an_already_writable_home(tmp_path, monkeypatch):
    writable = tmp_path / "real-home"
    writable.mkdir()
    monkeypatch.setenv("HOME", str(writable))
    evidence_root = tmp_path / "scans"
    evidence_root.mkdir()

    home = _ensure_writable_worker_home(evidence_root)

    assert home == writable
    assert os.environ["HOME"] == str(writable)
    assert not (evidence_root / ".worker-home").exists()


def test_dispatch_gate_fails_closed_for_a_newly_mapped_row(monkeypatch):
    """BEAAA-31700: a mapping row without the authored opt-in is not dispatched.

    The old gate was a denylist of probe ids, so the default for any newly
    registered family was "dispatch live". Selection is now positive: adding a
    row to the mapping cannot expand the live scheduled scan on its own.
    """
    from tools import active_test_registry
    from scripts import scheduled_scan

    baseline = scheduled_scan.load_garak_dispatch_rows()
    assert len(baseline) == 27

    real_build_registry = active_test_registry.build_registry

    def build_registry_with_new_family(*args, **kwargs):
        rows = list(real_build_registry(*args, **kwargs))
        template = next(
            row for row in rows if row.get("test_id") == "garak_beaaa152"
        )
        newcomer = dict(template)
        newcomer["case_id"] = "garak.propile.ProPILE"
        rows.append(newcomer)
        return rows

    monkeypatch.setattr(
        active_test_registry, "build_registry", build_registry_with_new_family
    )
    selected = scheduled_scan.load_garak_dispatch_rows()
    assert len(selected) == 27, "an un-opted-in row must stay out of live dispatch"
    assert "garak.propile.ProPILE" not in {row["case_id"] for row in selected}


def test_no_denylist_of_probe_ids_remains_in_the_dispatch_gate():
    source = (
        Path(__file__).resolve().parents[1] / "scripts" / "scheduled_scan.py"
    ).read_text(encoding="utf-8")
    assert "undispatched" not in source
    assert 'garak.sysprompt_extraction.SystemPromptExtraction"' not in source
