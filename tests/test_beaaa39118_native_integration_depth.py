"""BEAAA-39118: live scans use native Inspect/AgentDojo and tau end state."""
from __future__ import annotations

from types import SimpleNamespace

from tools.framework_workers import IsolatedFrameworkWorker, build_live_framework_workers


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        agent_endpoint="https://agent.example.test/run",
        agent_id="agent-39118",
        tenant_id="tenant-39118",
    )


def test_live_workers_select_native_inspect_and_agentdojo(monkeypatch, tmp_path) -> None:
    # Avoid constructing a real network-backed target; this test is about the
    # production worker selection boundary, not framework package execution.
    monkeypatch.setattr(
        "harness.customer_runner.customer_agent_http_runner_factory",
        lambda *_args: SimpleNamespace(model_id=lambda: "fixture-target"),
    )

    workers = build_live_framework_workers(
        _request(),
        "secret",
        evidence_root=tmp_path,
        judge_factory=lambda: SimpleNamespace(model_id=lambda: "fixture-judge"),
    )

    assert isinstance(workers["inspect"], IsolatedFrameworkWorker)
    assert isinstance(workers["agentdojo"], IsolatedFrameworkWorker)
    for worker_id in ("inspect", "agentdojo"):
        assert workers[worker_id].subject_adapter == {
            "kind": "customer_agent_http",
            "endpoint": "https://agent.example.test/run",
            "secret": "secret",
            "agent_id": "agent-39118",
        }

    # Tau now uses the same isolated native-worker boundary. The child adapter
    # records every target turn and applies deterministic database end-state
    # checks while preserving the customer-agent subject configuration.
    tau = workers["tau_bench"]
    assert isinstance(tau, IsolatedFrameworkWorker)
    assert tau.subject_adapter == {
        "kind": "customer_agent_http",
        "endpoint": "https://agent.example.test/run",
        "secret": "secret",
        "agent_id": "agent-39118",
    }
