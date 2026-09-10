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


def test_garak_beaaa152_probe_name_strips_garak_prefix(monkeypatch):
    """garak_beaaa152 case_id values carry a 'garak.' namespace prefix; the
    worker must strip it before calling _plugins.load_plugin."""
    loaded = []

    class FakeProbe:
        primary_detector = "always.Fail"
        def probe(self, gen):
            return []

    class FakeDetector:
        def detect(self, attempt):
            return []

    class FakePlugins:
        def load_plugin(self, name):
            loaded.append(name)
            if name.startswith("probes."):
                return FakeProbe()
            return FakeDetector()

    class FakeConfig:
        class run:
            generations = 1
            seed = 0
            eval_threshold = 0.5
        class transient:
            reportfile = None
        def load_base_config(self): pass

    import sys, types
    garak_mod = types.ModuleType("garak")
    garak_mod._config = FakeConfig()
    garak_mod._plugins = FakePlugins()
    generators_base = types.ModuleType("garak.generators.base")
    generators_base.Generator = type("FakeGenerator", (), {})
    evaluators_base = types.ModuleType("garak.evaluators.base")
    evaluators_base.ThresholdEvaluator = type(
        "FakeEvaluator", (), {"__init__": lambda self, threshold: None,
                               "test": lambda self, score: score > 0.5}
    )
    monkeypatch.setitem(sys.modules, "garak", garak_mod)
    monkeypatch.setitem(sys.modules, "garak.generators", types.ModuleType("garak.generators"))
    monkeypatch.setitem(sys.modules, "garak.generators.base", generators_base)
    monkeypatch.setitem(sys.modules, "garak.evaluators", types.ModuleType("garak.evaluators"))
    monkeypatch.setitem(sys.modules, "garak.evaluators.base", evaluators_base)

    _garak_adapter_result(
        WORKERS["garak"],
        {"case_id": "garak.misleading.FalseAssertion"},
        {"job_id": "j1", "seed": 0, "subject_adapter": {"kind": "customer_agent_http", "endpoint": "https://api.vapi.ai", "secret": "k", "agent_id": "a"}},
        "v1",
    )
    assert "probes.misleading.FalseAssertion" in loaded
    assert "probes.garak.misleading.FalseAssertion" not in loaded


def test_garak_worker_subprocess_stdout_is_clean_json(monkeypatch):
    """framework_workers.main() keeps framework progress off JSON stdout."""
    import io, json, sys
    import tools.framework_workers as fw

    def noisy_payload(_payload):
        sys.stdout.write("noise from framework lib\n")
        return {"status": "incomplete", "incomplete_reason": "test", "evidence_ref": None, "model_version": "v0", "schema": "s"}

    monkeypatch.setattr(fw, "run_worker_payload", noisy_payload)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    monkeypatch.setattr(sys, "stdout", buf)
    fw.main(["run"])
    captured = buf.getvalue()
    assert json.loads(captured)["status"] == "incomplete"
    assert "noise" not in captured


def test_garak_exception_detail_is_sanitized_collapsed_and_bounded():
    from tools.framework_workers import _garak_exception_reason
    secret = "vapi-secret-value"
    assistant_id = "11111111-1111-4111-8111-111111111111"
    exc = TypeError(f"bad call\nBearer {secret} assistant={assistant_id} token=also-secret " + "x" * 300)
    reason = _garak_exception_reason("native_probe_interface", exc, {"secret": secret, "agent_id": assistant_id})
    assert reason.startswith("native_probe_interface:TypeError:bad call ")
    assert "\n" not in reason
    assert secret not in reason
    assert assistant_id not in reason
    assert "also-secret" not in reason
    assert len(reason.rsplit(":", 1)[-1]) <= 200


def test_appended_garak_detail_preserves_scheduled_scan_bucket():
    from scripts.scheduled_scan import _incomplete_group
    original = "worker_unsupported_interface:garak:native_probe_interface:TypeError"
    assert _incomplete_group(original, target_calls=0) == "dispatch_failure"
    assert _incomplete_group(f"{original}:unexpected keyword argument", target_calls=0) == "dispatch_failure"
