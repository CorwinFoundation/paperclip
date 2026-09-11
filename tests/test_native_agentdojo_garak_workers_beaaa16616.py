"""Controlled native worker cases for BEAAA-16616."""
from __future__ import annotations

import json
from types import SimpleNamespace

from tools.framework_worker_registry import WORKERS
from tools.framework_workers import (
    GARAK_MIN_SAMPLE_SIZE,
    GARAK_NATIVE_PROBE_TIMEOUT_SECONDS,
    IsolatedFrameworkWorker,
    _agentdojo_adapter_result,
    _complete_subject,
    _garak_adapter_result,
    _seed_binding_for_subject,
    build_live_framework_workers,
    _result_dict,
    _run_subject_turn,
    _subject_messages,
)


def test_garak_subject_session_preserves_turns_and_returns_tool_results():
    first = json.dumps([{"role": "user", "content": "Cancel order 42"}], separators=(",", ":"))
    after_tool = json.dumps([
        {"role": "user", "content": "Cancel order 42"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "lookup", "arguments": {"id": 42}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "lookup", "content": {"status": "pending"}},
    ], separators=(",", ":"))
    subject = {
        "tools": {"lookup": {"result": {"status": "pending"}}},
        "responses": {
            first: {"tool_calls": [{"id": "c1", "name": "lookup", "arguments": {"id": 42}}]},
            after_tool: {"text": "Order 42 is pending; confirmation required."},
        },
    }

    text, history = _run_subject_turn(subject, _subject_messages("Cancel order 42"))

    assert text == "Order 42 is pending; confirmation required."
    assert [message["role"] for message in history] == ["user", "assistant", "tool", "assistant"]
    assert history[2]["content"] == {"status": "pending"}


def test_shared_worker_result_fields_default_to_none():
    result = _result_dict(
        status="complete", evidence_ref="evidence://fixture", incomplete_reason=None,
        model_version="v1",
    )

    assert result["numeric_result"] is None
    assert result["hit_count"] is None
    assert result["sample_size"] is None


def test_isolated_worker_preserves_rate_and_sample_counts(monkeypatch):
    import json
    import subprocess

    payload = {
        "status": "complete", "evidence_ref": "evidence://fixture/garak.json",
        "incomplete_reason": None, "model_version": "m1", "target_calls": 48,
        "judge_calls": 0, "duration_ms": 41, "numeric_result": 0.25,
        "hit_count": 12, "sample_size": 48,
    }
    monkeypatch.setattr(
        "tools.framework_workers.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout=json.dumps(payload), stderr=""
        ),
    )
    job = SimpleNamespace(
        job_id="j1", agent_id="a1", scanner_version="v1", corpus_version="c1",
        model_versions={"primary": "m1"}, seed=7,
    )

    result = IsolatedFrameworkWorker("garak")({}, job)

    assert result.numeric_result == result.hit_count / result.sample_size == 0.25


def test_garak_sub_floor_sample_is_incomplete(monkeypatch):
    class OneAttemptProbe:
        primary_detector = "always.Fail"
        detector_scores = [1.0]

        def probe(self, _gen):
            return [object()]

    _stub_garak(monkeypatch, OneAttemptProbe)
    result = _garak_adapter_result(
        WORKERS["garak"], {"case_id": "garak.misleading.FalseAssertion"},
        {"job_id": "j1", "seed": 0,
         "subject_adapter": {"kind": "customer_agent_http", "endpoint": "https://api.vapi.ai",
                             "secret": "k", "agent_id": "a"}},
        "v1",
    )

    assert result["status"] == "incomplete"
    assert result["incomplete_reason"] == f"garak_sample_size_below_floor:1<{GARAK_MIN_SAMPLE_SIZE}"
    assert result["numeric_result"] == 1.0
    assert result["hit_count"] == 1
    assert result["sample_size"] == 1

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


def test_vapi_transport_reports_job_seed_unbound():
    assert _seed_binding_for_subject(
        {
            "kind": "customer_agent_http",
            "endpoint": "https://api.vapi.ai",
            "secret": "test-secret",
            "agent_id": "11111111-1111-4111-8111-111111111111",
        },
        7,
    ) == "unbound"


def test_seed_transmitting_runner_reports_job_seed_bound(monkeypatch):
    class SeedTransmittingRunner:
        def seed_binding(self, seed):
            assert seed == 7
            return "bound"

    monkeypatch.setattr(
        "harness.customer_runner.customer_agent_http_runner_factory",
        lambda *_args: SeedTransmittingRunner(),
    )
    assert _seed_binding_for_subject(
        {"kind": "customer_agent_http", "endpoint": "https://example.test"}, 7
    ) == "bound"


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
    assert workers["garak"].timeout_seconds == GARAK_NATIVE_PROBE_TIMEOUT_SECONDS
    assert workers["promptfoo"] is generic


def test_isolated_worker_timeout_is_terminal_and_records_elapsed_time(monkeypatch):
    import subprocess

    monkeypatch.setattr(
        "tools.framework_workers.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        ),
    )
    job = SimpleNamespace(
        job_id="j1", agent_id="a1", scanner_version="v1", corpus_version="c1",
        model_versions={"primary": "m1"}, seed=7,
    )

    result = IsolatedFrameworkWorker("garak", timeout_seconds=3)({}, job)

    assert result.incomplete_reason == "worker_timeout:garak:3s"
    assert result.duration_ms >= 1


def test_isolated_worker_preserves_child_telemetry(monkeypatch):
    import json
    import subprocess

    payload = {
        "status": "incomplete", "evidence_ref": None,
        "incomplete_reason": "worker_native_invocation_error:garak:HTTP403",
        "model_version": "m1", "target_calls": 2, "judge_calls": 0,
        "duration_ms": 41,
    }
    monkeypatch.setattr(
        "tools.framework_workers.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout=json.dumps(payload), stderr=""
        ),
    )
    job = SimpleNamespace(
        job_id="j1", agent_id="a1", scanner_version="v1", corpus_version="c1",
        model_versions={"primary": "m1"}, seed=7,
    )

    result = IsolatedFrameworkWorker("garak")({}, job)

    assert (result.target_calls, result.judge_calls, result.duration_ms) == (2, 0, 41)


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
    # The worker imports garak.attempt.Message for the 0.16.0 generator return
    # contract, so the stub package has to carry it or the import fails closed.
    attempt_mod = types.ModuleType("garak.attempt")
    attempt_mod.Message = type("FakeMessage", (), {"__init__": lambda self, text: setattr(self, "text", text)})
    generators_base = types.ModuleType("garak.generators.base")
    generators_base.Generator = type("FakeGenerator", (), {})
    evaluators_base = types.ModuleType("garak.evaluators.base")
    evaluators_base.ThresholdEvaluator = type(
        "FakeEvaluator", (), {"__init__": lambda self, threshold: None,
                               "test": lambda self, score: score > 0.5}
    )
    monkeypatch.setitem(sys.modules, "garak", garak_mod)
    monkeypatch.setitem(sys.modules, "garak.attempt", attempt_mod)
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


def _stub_garak(monkeypatch, probe_cls):
    """Install a minimal fake garak package and return the module namespace."""
    import sys, types

    class FakeMessage:
        def __init__(self, text):
            self.text = text

    class FakeDetector:
        def detect(self, attempt):
            return list(getattr(probe_cls, "detector_scores", ()))

    class FakePlugins:
        def load_plugin(self, name):
            return probe_cls() if name.startswith("probes.") else FakeDetector()

    class FakeConfig:
        class run:
            generations = 1
            seed = 0
            eval_threshold = 0.5

        class transient:
            reportfile = None

        def load_base_config(self):
            pass

    garak_mod = types.ModuleType("garak")
    garak_mod._config = FakeConfig()
    garak_mod._plugins = FakePlugins()
    attempt_mod = types.ModuleType("garak.attempt")
    attempt_mod.Message = FakeMessage
    generators_base = types.ModuleType("garak.generators.base")
    generators_base.Generator = type("FakeGenerator", (), {"__init__": lambda self, name=None: None})
    evaluators_base = types.ModuleType("garak.evaluators.base")
    evaluators_base.ThresholdEvaluator = type(
        "FakeEvaluator", (), {"__init__": lambda self, threshold: None,
                              "test": lambda self, score: score > 0.5}
    )
    monkeypatch.setitem(sys.modules, "garak", garak_mod)
    monkeypatch.setitem(sys.modules, "garak.attempt", attempt_mod)
    monkeypatch.setitem(sys.modules, "garak.generators", types.ModuleType("garak.generators"))
    monkeypatch.setitem(sys.modules, "garak.generators.base", generators_base)
    monkeypatch.setitem(sys.modules, "garak.evaluators", types.ModuleType("garak.evaluators"))
    monkeypatch.setitem(sys.modules, "garak.evaluators.base", evaluators_base)
    return FakeMessage


def _run_garak_with_probe(monkeypatch, probe_cls):
    import tools.framework_workers as fw

    message_cls = _stub_garak(monkeypatch, probe_cls)
    seen = []

    original_run_subject_turn = fw._run_subject_turn

    def recording_run_subject_turn(subject, messages):
        seen.append(str(messages[-1]["content"]))
        return original_run_subject_turn(subject, messages)

    monkeypatch.setattr(fw, "_run_subject_turn", recording_run_subject_turn)
    fw._garak_adapter_result(
        WORKERS["garak"],
        {"case_id": "garak.misleading.FalseAssertion"},
        {"job_id": "j1", "seed": 0,
         "subject_adapter": {"response": "target replied"}},
        "v1",
    )
    return seen, message_cls


def test_garak_generator_unwraps_conversation_prompt(monkeypatch):
    """garak 0.16.0 hands _call_model a Conversation; the subject transport
    serialises JSON, so the object must be reduced to its text first.

    Passing it through raised `Object of type Conversation is not JSON
    serializable` on 21 of 27 rows in the BEAAA-31012 dispatch."""
    captured = {}

    class ConversationProbe:
        primary_detector = "always.Fail"

        def probe(self, gen):
            captured["outputs"] = gen._call_model(_FakeConversation("say the quiet part"), 1)
            return []

    seen, message_cls = _run_garak_with_probe(monkeypatch, ConversationProbe)

    assert seen == ["say the quiet part"]
    assert [type(item) for item in captured["outputs"]] == [message_cls]
    assert [item.text for item in captured["outputs"]] == ["target replied"]


def test_garak_generator_still_accepts_a_plain_string_prompt(monkeypatch):
    """Earlier garak pins passed a bare string. Keep that path working so a pin
    bump in either direction cannot silently break the transport."""
    class StringProbe:
        primary_detector = "always.Fail"

        def probe(self, gen):
            gen._call_model("plain string prompt", 1)
            return []

    seen, _ = _run_garak_with_probe(monkeypatch, StringProbe)
    assert seen == ["plain string prompt"]


def test_garak_generator_honours_generations_this_call(monkeypatch):
    class MultiProbe:
        primary_detector = "always.Fail"

        def probe(self, gen):
            captured_outputs.extend(gen._call_model(_FakeConversation("hi"), 3))
            return []

    captured_outputs: list = []
    seen, _ = _run_garak_with_probe(monkeypatch, MultiProbe)
    assert seen == ["hi", "hi", "hi"]
    assert len(captured_outputs) == 3


def test_garak_failed_target_call_records_telemetry(monkeypatch):
    import tools.framework_workers as fw

    class OneCallProbe:
        primary_detector = "always.Fail"

        def probe(self, gen):
            gen._call_model(_FakeConversation("will fail"), 1)
            return []

    _stub_garak(monkeypatch, OneCallProbe)
    monkeypatch.setattr(
        fw, "_complete_subject", lambda *_args: (_ for _ in ()).throw(RuntimeError("HTTP 403"))
    )

    result = fw._garak_adapter_result(
        WORKERS["garak"],
        {"case_id": "garak.misleading.FalseAssertion"},
        {"job_id": "j1", "seed": 0, "subject_adapter": {"kind": "customer_agent_http"}},
        "v1",
    )

    assert result["status"] == "incomplete"
    assert result["target_calls"] == 1
    assert result["judge_calls"] == 0
    assert result["duration_ms"] >= 1


def test_garak_prompt_text_handles_an_empty_conversation():
    from tools.framework_workers import _garak_prompt_text

    class Empty:
        def last_message(self, role=None):
            return None

    assert _garak_prompt_text(Empty()) == ""


class _FakeConversation:
    """Stands in for garak.attempt.Conversation: last_message() -> Message."""

    def __init__(self, text):
        self._text = text

    def last_message(self, role=None):
        return type("M", (), {"text": self._text})()
