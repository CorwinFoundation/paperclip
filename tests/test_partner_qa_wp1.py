from __future__ import annotations

import pytest

from api.scan_handlers import (
    CONNECTIVITY_PROMPT,
    HandlerError,
    ScanRuntime,
    _validate_agent_endpoint,
    connectivity_check_handler,
)
from harness.customer_runner import customer_agent_http_runner_factory
from harness.runner import RunnerError
from harness.kaltura_genie_browser import extract_assistant_narrative
from scanners.starter_pack import (
    STARTER_PACK_CORPUS_SNAPSHOT_HASH,
    STARTER_PACK_FAMILY_COUNTS,
    STARTER_PACK_ID,
    STARTER_PACK_ROSTER,
    STARTER_PACK_TEST_IDS,
)
from scanners.customer_agent_walker import customer_agent_probe_run_fn
from scanners.live_probe_orchestrator import ScanResult


def test_full_https_url_preserves_path_and_query() -> None:
    config = _validate_agent_endpoint({
        "url": "https://agent.example.test/hooks/a?version=1",
        "transport": "rest",
    })
    assert config["url"] == "https://agent.example.test/hooks/a?version=1"
    assert config["host"] == "agent.example.test"


@pytest.mark.parametrize("url", [
    "http://agent.example.test/hook",
    "https://user:secret@agent.example.test/hook",
    "https://agent.example.test/hook#fragment",
    "https://127.0.0.1/hook",
    "https://169.254.169.254/latest/meta-data",
])
def test_unsafe_full_urls_are_rejected(url: str) -> None:
    with pytest.raises(HandlerError):
        _validate_agent_endpoint({"url": url})


def test_host_only_shape_remains_compatible() -> None:
    config = _validate_agent_endpoint({"host": "agent.example.test"})
    assert config == {"host": "agent.example.test", "transport": "rest"}


def test_openai_chat_request_and_response_shapes() -> None:
    runner = customer_agent_http_runner_factory(
        "https://agent.example.test/v1/chat/completions",
        "secret",
        endpoint_config={
            "transport": "openai_chat",
            "model": "example-model",
            "system_prompt": "Keep answers short.",
            "auth": {"type": "bearer"},
        },
    )
    assert runner._request_payload("hello") == {
        "model": "example-model",
        "messages": [
            {"role": "system", "content": "Keep answers short."},
            {"role": "user", "content": "hello"},
        ],
    }
    assert runner._parse_response(
        '{"choices":[{"message":{"content":"world"}}]}'
    ) == "world"


def test_json_map_template_response_path_and_custom_auth() -> None:
    runner = customer_agent_http_runner_factory(
        "https://agent.example.test/hooks/a",
        "secret",
        endpoint_config={
            "transport": "json_map",
            "request_template": {"input": {"text": "{{prompt}}"}},
            "response_path": "result.answer",
            "auth": {"type": "custom_header", "header_name": "X-Agent-Key"},
        },
    )
    assert runner._request_payload("hello") == {"input": {"text": "hello"}}
    assert runner._parse_response('{"result":{"answer":"world"}}') == "world"
    assert runner._auth_headers == {"X-Agent-Key": "secret"}


def test_json_map_numeric_response_path() -> None:
    runner = customer_agent_http_runner_factory(
        "https://agent.example.test/hooks/a", "secret",
        endpoint_config={
            "transport": "json_map",
            "request_template": {"input": "{{prompt}}"},
            "response_path": "data.0.reply",
        },
    )
    assert runner._parse_response('{"data":[{"reply":"world"}]}') == "world"


def test_kaltura_genie_uses_scoped_ks_fresh_turn_and_assistant_text_only() -> None:
    runner = customer_agent_http_runner_factory(
        "https://genie.nvp1.ovp.kaltura.com/assistant/converse",
        "scoped-session",
        endpoint_config={"transport": "kaltura_genie", "auth": {"type": "bearer"}},
    )
    assert runner._auth_headers == {"Authorization": "KS scoped-session"}
    assert runner._request_payload("hello") == {
        "userMessage": "hello",
        "sse": False,
        "model_type": "fast",
        "add_message_in_db": False,
    }
    _, headers = runner._request_body("hello")
    assert headers == {"Accept": "application/x-ndjson"}
    stream = "\n".join([
        '{"role":"assistant","type":"think","content":"working"}',
        '{"role":"assistant","type":"text","content":"Hello "}',
        '{"role":"assistant","type":"tool","content":"hidden"}',
        '{"role":"assistant","type":"text","content":"world"}',
    ])
    assert runner._parse_response(stream) == "Hello world"


@pytest.mark.parametrize("stream", [
    '{"role":"assistant","type":"error","content":"failed"}',
    '{"event":"error","error":"failed"}',
    '{"role":"assistant","type":"think","content":"only telemetry"}',
    'not-json',
])
def test_kaltura_genie_fails_closed_without_clean_assistant_text(stream) -> None:
    runner = customer_agent_http_runner_factory(
        "https://genie.nvp1.ovp.kaltura.com/assistant/converse",
        "scoped-session",
        endpoint_config={"transport": "kaltura_genie", "auth": {"type": "bearer"}},
    )
    with pytest.raises(RunnerError):
        runner._parse_response(stream)


def test_kaltura_genie_endpoint_is_exactly_scoped() -> None:
    config = _validate_agent_endpoint({
        "url": "https://genie.nvp1.ovp.kaltura.com/assistant/converse",
        "transport": "kaltura_genie",
        "auth": {"type": "bearer"},
    })
    assert config["transport"] == "kaltura_genie"
    for url in (
        "https://learning.kaltura.com/",
        "https://genie.nvp1.ovp.kaltura.com/other",
    ):
        with pytest.raises(HandlerError):
            _validate_agent_endpoint({
                "url": url,
                "transport": "kaltura_genie",
                "auth": {"type": "bearer"},
            })


def test_kaltura_public_browser_transport_is_exactly_scoped_and_unauthenticated() -> None:
    config = _validate_agent_endpoint({
        "url": "https://learning.kaltura.com/",
        "transport": "kaltura_genie_browser",
        "auth": {"type": "none"},
    })
    runner = customer_agent_http_runner_factory(config["url"], "", endpoint_config=config)
    assert runner.model_id() == "kaltura-genie:public-learning-browser"
    for url in ("https://learning.kaltura.com/other", "https://example.test/"):
        with pytest.raises(HandlerError):
            _validate_agent_endpoint({
                "url": url,
                "transport": "kaltura_genie_browser",
                "auth": {"type": "none"},
            })


def test_kaltura_browser_extracts_only_two_narrative_fragments() -> None:
    class Nodes:
        def __init__(self, values): self.values = values
        def locator(self, selector): return Nodes(self.values)
        def count(self): return len(self.values)
        def nth(self, index): return Node(self.values[index])
    class Node:
        def __init__(self, value): self.value = value
        def locator(self, selector): return Nodes(self.value)
        def inner_text(self): return self.value

    article = Nodes([
        [
            ["user"],
            ["avatar", "first narrative", "second narrative", "social", "next steps"],
            ["spacer"],
        ],
    ])
    assert extract_assistant_narrative(article) == "first narrative\nsecond narrative"


@pytest.mark.parametrize("article", [
    [],
    [[[["user"], ["assistant"], ["spacer"]]], [[["unexpected"]]]],
    [[["user"], ["assistant"], ["spacer"], ["unexpected"]]],
])
def test_kaltura_browser_rejects_changed_nested_article_structure(article) -> None:
    class Nodes:
        def __init__(self, values): self.values = values
        def locator(self, selector): return Nodes(self.values)
        def count(self): return len(self.values)
        def nth(self, index): return Node(self.values[index])
    class Node:
        def __init__(self, value): self.value = value
        def locator(self, selector): return Nodes(self.value)
        def inner_text(self): return self.value

    with pytest.raises(RunnerError, match="structure changed"):
        extract_assistant_narrative(Nodes(article))


@pytest.mark.parametrize("template", [
    {"input": "missing"},
    {"a": "{{prompt}}", "b": ["{{prompt}}"]},
])
def test_json_map_requires_exactly_one_prompt_placeholder(template) -> None:
    with pytest.raises(HandlerError) as exc:
        _validate_agent_endpoint({
            "url": "https://agent.example.test/hook",
            "transport": "json_map",
            "request_template": template,
            "response_path": "reply",
        })
    assert "agent_endpoint_prompt_placeholder_invalid" in str(exc.value)


def test_starter_roster_pins_exact_membership_and_family_counts() -> None:
    assert len(STARTER_PACK_ROSTER) == 100
    assert len(STARTER_PACK_TEST_IDS) == len(set(STARTER_PACK_TEST_IDS)) == 100
    assert STARTER_PACK_TEST_IDS[0] == "p1_1_factuality_golden:P1.1-FAC-001"
    assert STARTER_PACK_TEST_IDS[-1] == "p3_6_output_attack:P3.6-OUT-010"
    assert STARTER_PACK_FAMILY_COUNTS == {
        "p1_1_factuality_golden": 10,
        "p1_3_consistency_golden": 10,
        "p1_4_refusal_golden": 10,
        "p1_5_calibration_golden": 10,
        "p1_6_eval_coverage_breadth_golden": 10,
        "p2_5_sentiment_skew_golden": 10,
        "p3_2_indirect_injection_floor30": 10,
        "p3_3_secret_exfil_floor30": 10,
        "p3_5_multiturn_jailbreak": 10,
        "p3_6_output_attack": 10,
    }


def test_starter_walker_smoke_emits_exactly_100_distinct_calls() -> None:
    class Runner:
        pack_id = STARTER_PACK_ID
        surface_seed = 0
        operational_scan_deadline_seconds = None

        def __init__(self):
            self.prompts = []

        def complete(self, prompt):
            self.prompts.append(prompt)
            return "safe response"

    runner = Runner()
    rows = list(customer_agent_probe_run_fn(runner))
    assert len(rows) == len(runner.prompts) == 100
    assert len(set(runner.prompts)) == 100


def test_no_auth_does_not_retain_secret_in_headers() -> None:
    runner = customer_agent_http_runner_factory(
        "https://agent.example.test/hooks/a",
        "must-not-be-sent",
        endpoint_config={"transport": "rest", "auth": {"type": "none"}},
    )
    assert runner._auth_headers == {}


def test_connectivity_check_returns_canonical_success_shape() -> None:
    observed = {}

    class Runner:
        def complete(self, prompt: str) -> str:
            observed["prompt"] = prompt
            return "OK"

    def factory(url, secret, agent_id, *, endpoint_config):
        observed.update(url=url, secret=secret, agent_id=agent_id, config=endpoint_config)
        return Runner()

    status, payload = connectivity_check_handler({
        "agent_endpoint": {"url": "https://agent.example.test/hook"},
        "credential_secret": "ephemeral",
    }, runner_factory=factory)
    assert status == 200
    assert payload == {"success": True, "error": None}
    assert observed["prompt"] == CONNECTIVITY_PROMPT
    assert observed["secret"] == "ephemeral"


def test_connectivity_check_redacts_transport_failure() -> None:
    class Runner:
        def complete(self, prompt: str) -> str:
            raise RunnerError("credential=super-secret endpoint body")

    status, payload = connectivity_check_handler({
        "agent_endpoint": {"url": "https://agent.example.test/hook"},
        "credential_secret": "super-secret",
    }, runner_factory=lambda *args, **kwargs: Runner())
    assert status == 200
    assert payload == {"success": False, "error": "connection_failed"}
    assert "super-secret" not in str(payload)


def test_starter_pack_has_fixed_identity_and_exact_count(monkeypatch) -> None:
    monkeypatch.setenv("ARE_SCAN_TIER", "on")
    # BEAAA-38895: @1 requests run @2 by default; this pins the legacy pack.
    monkeypatch.setenv("BACKBOND_STARTER_PACK_LEGACY_V1", "1")
    captured = {}

    def run_scan(request, secret):
        captured["request"] = request
        return ScanResult(
            scan_id=request.scan_id,
            status="complete",
            scanner_version=request.scanner_version,
            corpus_version=request.corpus_version,
            rows_emitted=100,
            k_replicates=request.k_replicates,
        )

    runtime = ScanRuntime(run_scan_fn=run_scan)
    body = {
        "tenant_id": "tenant-001",
        "agent_endpoint": {"host": "agent.example.test"},
        "credential_ref": "partner.qa",
        "credential_secret": "ephemeral",
        "scanner_version": "0.5.0",
        "pack_id": STARTER_PACK_ID,
        "scan_mode": "live_probe",
    }
    from api.scan_handlers import scan_handler
    status, payload = scan_handler(
        body,
        tenant_header="tenant-001",
        runtime=runtime,
        scan_id_factory=lambda: "starter-scan",
    )
    assert status == 202
    assert payload["pack_id"] == STARTER_PACK_ID
    assert payload["test_count"] == 100
    assert payload["corpus_snapshot_hash"] == STARTER_PACK_CORPUS_SNAPSHOT_HASH
    assert captured["request"].pack_id == STARTER_PACK_ID
