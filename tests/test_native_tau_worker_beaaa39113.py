from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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
    class Action:
        def __init__(self, name, kwargs):
            self.name, self.kwargs = name, kwargs

    class Environment:
        wiki = "Retail policy"
        task = SimpleNamespace(instruction="Refund the duplicate charge")
        task_index = 0
        tools_info = [{"type": "function", "function": {"name": "refund_charge"}}]

        def __init__(self):
            self.data = {"payments": {"refunded_cents": 0}}
            self.actions = []

        def step(self, action):
            self.actions.append(action)
            self.data["payments"]["refunded_cents"] = action.kwargs["amount"]
            return SimpleNamespace(observation="refund recorded")

        def calculate_reward(self):
            class Reward:
                reward = 1.0

                def model_dump(self, *, mode):
                    assert mode == "json"
                    return {"reward": 1.0, "info": {"database_end_state": True}}

            return Reward()

    env = Environment()
    monkeypatch.setattr(
        "tools.framework_workers._load_native_tau_environment",
        lambda _case_id: (env, Action, "respond"),
    )

    responses = iter([
        {"tool_calls": [{
            "id": "call-1",
            "function": {"name": "refund_charge", "arguments": '{"amount": 1200}'},
        }]},
        {"content": "The duplicate charge was refunded."},
    ])

    class Runner:
        def complete_messages(self, _messages, *, tools):
            assert tools == env.tools_info
            return next(responses)

        def execution_evidence(self):
            return {"request_count": 2, "estimated_cost_usd": 0.01}

        def seed_binding(self, seed):
            return f"bound:{seed}"

    monkeypatch.setattr(
        "harness.customer_runner.customer_agent_http_runner_factory",
        lambda *_args: Runner(),
    )
    row = {"case_id": SCENARIOS[0].stable_id, "scoring_active": False}

    result = _tau_bench_adapter_result(WORKERS["tau_bench"], row, _job(tmp_path), "target-v1")

    assert result["status"] == "complete"
    assert result["verdict"] == "pass"
    assert result["target_calls"] == 2
    evidence = json.loads(Path(result["evidence_ref"].removeprefix("file://")).read_text())
    assert evidence["framework"].endswith(WORKERS["tau_bench"].framework_version)
    assert evidence["attempt_id"] == f"scan-39113:{SCENARIOS[0].stable_id}:4"
    assert evidence["native_task_index"] == 0
    assert evidence["tool_trace"] == [{
        "name": "refund_charge", "arguments": {"amount": 1200},
        "observation": "refund recorded",
    }]
    assert evidence["before_state"]["payments"]["refunded_cents"] == 0
    assert evidence["after_state"]["payments"]["refunded_cents"] == 1200
    assert evidence["oracle"] == {
        "kind": "tau_bench.Env.calculate_reward", "reward": 1.0,
        "reward_info": {"info": {"database_end_state": True}, "reward": 1.0},
        "verdict": "pass",
    }
    assert evidence["spend"] == {"estimated_cost_usd": 0.01, "request_count": 2}


def test_tau_native_worker_rejects_scoring_and_unknown_cases(tmp_path):
    job = _job(tmp_path)
    scoring = _tau_bench_adapter_result(
        WORKERS["tau_bench"], {"case_id": SCENARIOS[0].stable_id, "scoring_active": True}, job, "v1"
    )
    assert scoring["incomplete_reason"].endswith("tau_case_must_be_report_only")
