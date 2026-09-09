"""Controlled native worker cases for BEAAA-16616."""
from __future__ import annotations

from types import SimpleNamespace

from tools.framework_worker_registry import WORKERS
from tools.framework_workers import (
    _agentdojo_adapter_result,
    _complete_subject,
    _garak_adapter_result,
    build_live_framework_workers,
)

def test_native_workers_return_terminal_incomplete_for_unsupported_target():
    for worker_id, adapter in (
        ("agentdojo", _agentdojo_adapter_result),
        ("garak", _garak_adapter_result),
    ):
        result = adapter(WORKERS[worker_id], {}, {"job_id": "scan"}, "local-target@1")
        assert result["status"] == "incomplete"
        assert result["evidence_ref"] is None
        assert result["incomplete_reason"] == f"worker_unsupported_interface:{worker_id}:missing_subject_adapter"


def test_garak_subject_uses_production_vapi_transport(monkeypatch):
    calls = []

    class Runner:
        def complete(self, prompt):
            calls.append(("complete", prompt))
            return "vapi response"

    def factory(endpoint, secret, agent_id):
        calls.append(("factory", endpoint, secret, agent_id))
        return Runner()

    monkeypatch.setattr(
        "harness.customer_runner.customer_agent_http_runner_factory", factory
    )

    response = _complete_subject(
        {
            "kind": "customer_agent_http",
            "endpoint": "https://api.vapi.ai",
            "secret": "test-secret",
            "agent_id": "11111111-1111-4111-8111-111111111111",
        },
        "native garak prompt",
    )

    assert response == "vapi response"
    assert calls == [
        (
            "factory",
            "https://api.vapi.ai",
            "test-secret",
            "11111111-1111-4111-8111-111111111111",
        ),
        ("complete", "native garak prompt"),
    ]


def test_live_worker_factory_replaces_garak_text_materializer(monkeypatch):
    generic = object()
    monkeypatch.setattr(
        "tools.registered_case_executor.build_registered_case_workers",
        lambda _request, _secret: {"garak": generic, "promptfoo": generic},
    )
    request = SimpleNamespace(
        agent_endpoint="https://api.vapi.ai",
        agent_id="11111111-1111-4111-8111-111111111111",
    )

    workers = build_live_framework_workers(request, "vapi-secret")

    assert workers["garak"] is not generic
    assert workers["garak"].subject_adapter == {
        "kind": "customer_agent_http",
        "endpoint": "https://api.vapi.ai",
        "secret": "vapi-secret",
        "agent_id": "11111111-1111-4111-8111-111111111111",
    }
    assert workers["promptfoo"] is generic
