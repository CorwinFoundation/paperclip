from __future__ import annotations

import json
import shlex
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    assert evidence["spend"] == {"estimated_cost_usd": None, "request_count": None}
    replay_path = Path(evidence["replay_payload"].removeprefix("file://"))
    assert replay_path.is_file()
    replay = json.loads(replay_path.read_text())
    assert replay["job"]["subject_adapter"]["secret"] == "${BACKBOND_REPLAY_SECRET}"
    assert "secret" not in evidence["replay_command"]
    assert shlex.split(evidence["replay_command"])[-1] == str(replay_path)


def test_tau_native_worker_uses_real_customer_http_runner(monkeypatch, tmp_path):
    class Action:
        def __init__(self, name, kwargs):
            self.name, self.kwargs = name, kwargs

    class Environment:
        wiki = "Retail policy"
        task = SimpleNamespace(instruction="Refund the duplicate charge")
        task_index = 0
        tools_info = [{"type": "function", "function": {"name": "refund_charge"}}]

        def __init__(self):
            self.data = {"refunded_cents": 0}
            self.actions = []

        def step(self, action):
            self.actions.append(action)
            self.data["refunded_cents"] = action.kwargs["amount"]
            return SimpleNamespace(observation="refund recorded")

        def calculate_reward(self):
            return SimpleNamespace(reward=1.0)

    env = Environment()
    monkeypatch.setattr(
        "tools.framework_workers._load_native_tau_environment",
        lambda _case_id: (env, Action, "respond"),
    )
    monkeypatch.setattr(
        "harness.egress._is_forbidden_resolved_ip",
        lambda address: address != "127.0.0.1",
    )
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            response = (
                {"tool_calls": [{
                    "id": "call-1",
                    "function": {"name": "refund_charge", "arguments": '{"amount": 1200}'},
                }]}
                if len(requests) == 1
                else {"content": "The duplicate charge was refunded."}
            )
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        job = _job(tmp_path)
        job["subject_adapter"]["endpoint"] = f"http://127.0.0.1:{server.server_port}/agent"
        result = _tau_bench_adapter_result(
            WORKERS["tau_bench"],
            {"case_id": SCENARIOS[0].stable_id, "scoring_active": False},
            job,
            "target-v1",
        )
    finally:
        server.shutdown()
        thread.join()

    assert result["status"] == "complete"
    assert result["verdict"] == "pass"
    assert result["target_calls"] == 2
    assert requests[0]["messages"][:2] == [
        {"role": "system", "content": env.wiki},
        {"role": "user", "content": env.task.instruction},
    ]
    assert requests[0]["tools"] == env.tools_info
    assert requests[1]["messages"][-1] == {
        "role": "tool", "name": "refund_charge", "tool_call_id": "call-1",
        "content": "refund recorded",
    }


def test_tau_native_worker_rejects_scoring_and_unknown_cases(tmp_path):
    job = _job(tmp_path)
    scoring = _tau_bench_adapter_result(
        WORKERS["tau_bench"], {"case_id": SCENARIOS[0].stable_id, "scoring_active": True}, job, "v1"
    )
    assert scoring["incomplete_reason"].endswith("tau_case_must_be_report_only")


def test_tau_preserves_voice_adapters_as_unsupported(tmp_path):
    for kind in ("vapi", "syllable"):
        job = _job(tmp_path)
        job["subject_adapter"] = {"kind": kind}
        result = _tau_bench_adapter_result(
            WORKERS["tau_bench"],
            {"case_id": SCENARIOS[0].stable_id, "scoring_active": False},
            job,
            "v1",
        )
        assert result["status"] == "incomplete"
        assert result["incomplete_reason"].endswith("unsupported_subject_adapter")


def test_tau_unknown_cost_is_null_not_zero(monkeypatch, tmp_path):
    class Action:
        def __init__(self, name, kwargs):
            self.name, self.kwargs = name, kwargs

    env = SimpleNamespace(
        wiki="policy", task=SimpleNamespace(instruction="reply"), task_index=0,
        tools_info=[], data={}, actions=[],
        calculate_reward=lambda: SimpleNamespace(reward=1.0),
    )
    monkeypatch.setattr(
        "tools.framework_workers._load_native_tau_environment",
        lambda _case_id: (env, Action, "respond"),
    )
    class Runner:
        def complete_messages(self, _messages, *, tools):
            return {"content": "done"}
        def seed_binding(self, seed):
            return f"bound:{seed}"
    monkeypatch.setattr(
        "harness.customer_runner.customer_agent_http_runner_factory",
        lambda *_args: Runner(),
    )
    result = _tau_bench_adapter_result(
        WORKERS["tau_bench"],
        {"case_id": SCENARIOS[0].stable_id, "scoring_active": False},
        _job(tmp_path),
        "v1",
    )
    evidence = json.loads(Path(result["evidence_ref"].removeprefix("file://")).read_text())
    assert evidence["spend"] == {"estimated_cost_usd": None, "request_count": None}
