"""Controlled native worker cases for BEAAA-16616."""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass

from tools.framework_worker_registry import WORKERS
from tools.framework_workers import _agentdojo_adapter_result, _garak_adapter_result


class _Environment:
    def __init__(self, state: str = "initial") -> None:
        self.state = state

    def model_copy(self, *, deep: bool):
        assert deep is True
        return _Environment(self.state)


def test_agentdojo_native_suite_executes_target_and_verifies_end_state(monkeypatch):
    events: list[str] = []

    class Task:
        PROMPT = "perform the controlled workspace action"

        def utility(self, response, before, after):
            events.append("terminal-verifier")
            return response == "TARGET_OK" and before.state == "initial" and after.state == "changed"

    class Suite:
        def load_and_inject_default_environment(self, injections):
            events.append("load-environment")
            assert injections == {}
            return _Environment()

        def get_user_task_by_id(self, task_id):
            events.append(f"load-task:{task_id}")
            return Task()

        def apply_tool_calls(self, environment, tool_calls):
            events.append("apply-tools")
            assert tool_calls == [{"name": "controlled_write"}]
            return _Environment("changed")

    load_suites = types.ModuleType("agentdojo.task_suite.load_suites")
    load_suites.get_suite = lambda version, name: (events.append(f"get-suite:{version}:{name}") or Suite())
    monkeypatch.setitem(sys.modules, "agentdojo", types.ModuleType("agentdojo"))
    monkeypatch.setitem(sys.modules, "agentdojo.task_suite", types.ModuleType("agentdojo.task_suite"))
    monkeypatch.setitem(sys.modules, "agentdojo.task_suite.load_suites", load_suites)

    result = _agentdojo_adapter_result(
        WORKERS["agentdojo"],
        {
            "test_id": "agentdojo",
            "case_id": "controlled.workspace",
            "agentdojo_suite": "workspace",
            "agentdojo_task_id": "user_task_0",
            "agentdojo_task_kind": "user",
            "agentdojo_benchmark_version": "v1.2.2",
            "subject_adapter": {
                "response": "TARGET_OK",
                "tool_calls": [{"name": "controlled_write"}],
            },
            "evidence_uri_template": "evidence://{scan_id}/{test_id}/{case_id}.json",
        },
        {"job_id": "scan-1"},
        "local-target@1",
    )

    assert result["status"] == "complete"
    assert result["evidence_ref"] == "evidence://scan-1/agentdojo/controlled.workspace.json"
    assert events == [
        "get-suite:v1.2.2:workspace",
        "load-environment",
        "load-task:user_task_0",
        "apply-tools",
        "terminal-verifier",
    ]


def test_garak_native_probe_invokes_real_target_and_terminal_verdict(monkeypatch):
    events: list[str] = []

    class Generator:
        def __init__(self, name="", config_root=None):
            self.name = name

        def generate(self, prompt, generations_this_call=1):
            return self._call_model(prompt, generations_this_call)

    @dataclass
    class Attempt:
        outputs: list[str]

    class Probe:
        def probe(self, generator):
            events.append("native-probe")
            return [Attempt(generator.generate("GARAK_CONTROLLED_PROMPT"))]

    config = types.SimpleNamespace(
        transient=types.SimpleNamespace(reportfile=None),
        run=types.SimpleNamespace(generations=5),
    )
    plugins = types.SimpleNamespace(
        load_plugin=lambda name: (events.append(f"load:{name}") or Probe())
    )
    garak = types.ModuleType("garak")
    garak._config = config
    garak._plugins = plugins
    generators = types.ModuleType("garak.generators")
    base = types.ModuleType("garak.generators.base")
    base.Generator = Generator
    monkeypatch.setitem(sys.modules, "garak", garak)
    monkeypatch.setitem(sys.modules, "garak.generators", generators)
    monkeypatch.setitem(sys.modules, "garak.generators.base", base)

    result = _garak_adapter_result(
        WORKERS["garak"],
        {
            "test_id": "garak",
            "case_id": "active_registry:garak:dan.AntiDAN",
            "garak_probe": "dan.AntiDAN",
            "expected_response": "GARAK_TARGET_OK",
            "subject_adapter": {
                "responses": {"GARAK_CONTROLLED_PROMPT": "GARAK_TARGET_OK"}
            },
            "evidence_uri_template": "evidence://{scan_id}/{test_id}/{case_id}.json",
        },
        {"job_id": "scan-2"},
        "local-target@1",
    )

    assert result["status"] == "complete"
    assert result["evidence_ref"].endswith("/garak/active_registry:garak:dan.AntiDAN.json")
    assert events == ["load:probes.dan.AntiDAN", "native-probe"]


def test_native_workers_return_terminal_incomplete_for_unsupported_target():
    for worker_id, adapter in (
        ("agentdojo", _agentdojo_adapter_result),
        ("garak", _garak_adapter_result),
    ):
        result = adapter(WORKERS[worker_id], {}, {"job_id": "scan"}, "local-target@1")
        assert result["status"] == "incomplete"
        assert result["evidence_ref"] is None
        assert result["incomplete_reason"] == f"worker_unsupported_interface:{worker_id}:missing_subject_adapter"
