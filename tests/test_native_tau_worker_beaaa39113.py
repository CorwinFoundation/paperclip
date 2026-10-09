from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from evals.provisional_tau_support_crm import SCENARIOS
from tools.framework_worker_registry import WORKERS
from tools.framework_workers import _tau_bench_adapter_result, build_live_framework_workers


def _job(tmp_path: Path) -> dict[str, object]:
    return {
        "job_id": "scan-39113", "agent_id": "agent-1", "seed": 4,
        "evidence_dir": str(tmp_path),
        "subject_adapter": {
            "kind": "customer_agent_http", "endpoint": "https://target.invalid",
            "secret": "secret", "agent_id": "agent-1",
        },
    }


def test_live_factory_routes_tau_through_isolated_worker(monkeypatch):
    monkeypatch.setattr(
        "tools.registered_case_executor.build_registered_case_workers",
        lambda *_args, **_kwargs: {worker_id: object() for worker_id in WORKERS},
    )
    request = SimpleNamespace(agent_endpoint="https://target.invalid", agent_id="agent-1")
    workers = build_live_framework_workers(request, "secret")
    assert workers["tau_bench"].worker_id == "tau_bench"
    assert workers["tau_bench"].subject_adapter["endpoint"] == request.agent_endpoint


def test_tau_native_worker_persists_trace_state_oracle_and_attempt(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "tau_bench", ModuleType("tau_bench"))
    monkeypatch.setattr(
        "tools.registered_case_executor._http_tau_session_factory",
        lambda *_args: object(),
    )
    outcome = {
        "verdict": "pass",
        "transcript": {"turns": [{"assistant_text": "checked"}, {"assistant_text": "done"}]},
        "tool_call_log": [{"call": {"tool_name": "lookup_charge"}}],
        "observed_state": {"payments": {"refunded_cents": 1200}},
        "checks": {"database_end_state": True, "tool_call_evidence": True},
    }
    monkeypatch.setattr("evals.provisional_tau_support_crm.run_scenario", lambda *_args, **_kwargs: outcome)
    row = {"case_id": SCENARIOS[0].stable_id, "scoring_active": False}

    result = _tau_bench_adapter_result(WORKERS["tau_bench"], row, _job(tmp_path), "target-v1")

    assert result["status"] == "complete"
    assert result["verdict"] == "pass"
    assert result["target_calls"] == 2
    evidence = json.loads(Path(result["evidence_ref"].removeprefix("file://")).read_text())
    assert evidence["framework"].endswith(WORKERS["tau_bench"].framework_version)
    assert evidence["attempt_id"] == f"scan-39113:{SCENARIOS[0].stable_id}:4"
    assert evidence["tool_trace"] == outcome["tool_call_log"]
    assert evidence["after_state"] == outcome["observed_state"]
    assert evidence["oracle"]["verdict"] == "pass"


def test_tau_native_worker_rejects_scoring_and_unknown_cases(tmp_path):
    job = _job(tmp_path)
    scoring = _tau_bench_adapter_result(
        WORKERS["tau_bench"], {"case_id": SCENARIOS[0].stable_id, "scoring_active": True}, job, "v1"
    )
    assert scoring["incomplete_reason"].endswith("tau_case_must_be_report_only")
