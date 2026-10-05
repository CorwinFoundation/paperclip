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
