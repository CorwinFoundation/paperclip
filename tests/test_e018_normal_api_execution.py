"""E018 scope review uses configured HTTP transport and bounded real judging."""
from contextlib import contextmanager
from dataclasses import replace
import json
import sys
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.scan_handlers import ScanRuntime
from harness import customer_runner
from harness.bench.aiuc_path_b_lane_a import embedded_cases
from harness.live_tool_agent import DISCLOSURE_TARGET_PROFILE, TARGET_SYSTEM_POLICIES
from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.framework_workers import build_live_framework_workers
from tools.registered_case_executor import (
    BudgetExceeded, DISCLOSURE_JUDGE_RESPONSE_SCHEMA, DISCLOSURE_JUDGE_SYSTEM_PROMPT,
    DISCLOSURE_SCOPE_ID, ScanBudget, ScanBudgetLedger,
    _scoped_scan_budget,
)
import tools.registered_case_executor as execution


def disclosure_scope():
    return {
        "mode": "scope_review", "scope_ids": [DISCLOSURE_SCOPE_ID],
        "requested_case_ids": sorted(c["case_id"] for c in embedded_cases("ai_disclosure")),
    }


def request(scope=None, max_cost_usd=0.10):
    return SimpleNamespace(operations_scope=scope, max_cost_usd=max_cost_usd)


def test_disclosure_review_has_one_judge_per_case_and_hard_cost_ceiling():
    budget = _scoped_scan_budget(request(disclosure_scope(), 10), ScanBudget())
    assert (budget.max_target_calls, budget.max_judge_calls) == (40, 24)
    assert budget.max_estimated_judge_cost_usd == 0.10
    assert budget.judge_max_output_tokens_per_call == 128
    ledger = ScanBudgetLedger(budget)
    for _ in range(24):
        ledger.reserve_judge_call("short fixture")
    with pytest.raises(BudgetExceeded, match="max_judge_calls"):
        ledger.reserve_judge_call("one too many")


@pytest.mark.parametrize("scope_change", [
    {"mode": "targeted"}, {"mode": "smoke"}, {"mode": "full"},
    {"scope_ids": [DISCLOSURE_SCOPE_ID, "OPS.OTHER"]},
    {"scope_ids": ["OPS.OTHER"]},
    {"requested_case_ids": ["AD-BYP-05"]},
    {"requested_case_ids": ["TENANT-01"]},
])
def test_other_or_inexact_scopes_cannot_purchase_judges(scope_change):
    scope = {**disclosure_scope(), **scope_change}
    budget = _scoped_scan_budget(request(scope), ScanBudget())
    assert budget.max_judge_calls == 0
    assert budget.max_estimated_judge_cost_usd == 0


def test_scope_budget_preserves_lower_server_and_request_limits():
    low = replace(ScanBudget(), max_target_calls=3, max_judge_calls=2,
                  max_estimated_judge_cost_usd=0.02)
    budget = _scoped_scan_budget(request(disclosure_scope(), 0.01), low)
    assert (budget.max_target_calls, budget.max_judge_calls) == (3, 2)
    assert budget.max_estimated_judge_cost_usd == 0.01
    zero = _scoped_scan_budget(request(disclosure_scope()), replace(low, max_judge_calls=0))
    assert zero.max_judge_calls == 0
    assert _scoped_scan_budget(request(), low) is low
    ledger = ScanBudgetLedger(replace(budget, max_estimated_judge_cost_usd=0.000001))
    with pytest.raises(BudgetExceeded, match="max_estimated_judge_cost_usd"):
        ledger.reserve_judge_call("cost cap is enforced before provider dispatch")
    assert ledger.judge_calls == 0


@pytest.mark.parametrize("provider", ["anthropic", "openrouter"])
def test_production_judge_applies_the_reserved_output_limit(monkeypatch, provider):
    captured = {}
    monkeypatch.setenv("ARE_FULL_SCAN_JUDGE_PROVIDER", provider)
    monkeypatch.setenv("ARE_FULL_SCAN_JUDGE_MAX_OUTPUT_TOKENS", "256")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")

    def sdk(**kwargs):
        captured.update(kwargs)
        return MockRunner("judge-fixture", lambda _: "unused")

    monkeypatch.setattr(execution, "AnthropicRunner", sdk)
    monkeypatch.setattr(execution, "OpenRouterRunner", sdk)
    execution.default_judge_runner(max_output_tokens=128, system_prompt=DISCLOSURE_JUDGE_SYSTEM_PROMPT,
                                   response_schema=DISCLOSURE_JUDGE_RESPONSE_SCHEMA)
    assert captured["max_tokens"] == 128
    assert captured["system_prompt"] == DISCLOSURE_JUDGE_SYSTEM_PROMPT
    assert captured["response_schema"] == DISCLOSURE_JUDGE_RESPONSE_SCHEMA
    monkeypatch.setenv("ARE_FULL_SCAN_JUDGE_MAX_OUTPUT_TOKENS", "64")
    execution.default_judge_runner(max_output_tokens=128)
    assert captured["max_tokens"] == 64


@pytest.mark.parametrize("batch", [False, True])
def test_native_schema_uses_pinned_sdk_extra_body_or_batch_wire_params(monkeypatch, batch):
    captured = {}
    message = SimpleNamespace(content=[SimpleNamespace(type="text", text='{"verdict":"pass","score":1.0}')],
                              usage=SimpleNamespace(input_tokens=1, output_tokens=2))
    batches = SimpleNamespace(
        create=lambda **kw: captured.update(kw) or SimpleNamespace(id="batch", processing_status="ended"),
        results=lambda _: iter([SimpleNamespace(result=SimpleNamespace(type="succeeded", message=message))]),
    )
    messages = SimpleNamespace(create=lambda **kw: captured.update(kw) or message, batches=batches)
    fake = SimpleNamespace(Anthropic=lambda: SimpleNamespace(messages=messages),
                           BadRequestError=type("BadRequestError", (Exception,), {}))
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture")
    runner = execution.AnthropicRunner("claude-fixture", max_tokens=128, batch=batch,
                                       response_schema=DISCLOSURE_JUDGE_RESPONSE_SCHEMA)
    assert json.loads(runner.complete("grade"))["verdict"] == "pass"
    payload = captured["requests"][0]["params"] if batch else captured
    wire_extra = payload if batch else payload["extra_body"]
    assert wire_extra["output_config"]["format"] == {
        "type": "json_schema", "schema": DISCLOSURE_JUDGE_RESPONSE_SCHEMA,
    }
    assert payload["max_tokens"] == 128
    if batch:
        assert "extra_body" not in payload


def test_openrouter_schema_and_token_cap_reach_provider_wire(monkeypatch):
    captured = {}
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    def post(url, **kwargs):
        captured.update(kwargs["json"])
        return SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"message": {"content": '{"verdict":"pass","score":1.0}'}}],
        })
    monkeypatch.setattr("harness.egress.post", post)
    runner = execution.OpenRouterRunner("claude-fixture", max_tokens=128,
                                        response_schema=DISCLOSURE_JUDGE_RESPONSE_SCHEMA)
    assert json.loads(runner.complete("grade"))["verdict"] == "pass"
    assert captured["max_tokens"] == 128
    assert captured["response_format"]["json_schema"]["schema"] == DISCLOSURE_JUDGE_RESPONSE_SCHEMA


@pytest.mark.parametrize("transport", ["openai_chat", "json_map"])
def test_normal_http_api_preserves_target_config_and_grades_disclosure(
    monkeypatch, tmp_path, transport,
):
    monkeypatch.setenv("ARE_RUNTIME_PROFILE", "local_dev")
    monkeypatch.delenv("ARE_API_AUTH_TOKENS_JSON", raising=False)
    monkeypatch.setenv("ARE_PRODUCTION_SCAN_WIRING", "off")
    monkeypatch.setenv("ARE_FULL_SCAN_ATTEMPT_LEDGER_PATH", str(tmp_path / "attempts.sqlite3"))
    monkeypatch.setattr("api.scan_handlers._verify_live_egress_fail_closed", lambda **kw: None)
    received, judged, executors = [], [], []
    policy = TARGET_SYSTEM_POLICIES[DISCLOSURE_TARGET_PROFILE]
    reply = json.dumps({"speaker_type": "ai", "automated": True,
                        "message": "Hello. I am an automated AI assistant."})

    def handle(wire):
        payload = json.loads(wire.content)
        received.append(payload)
        assert payload["model"] == "self-owned-claude-fixture"
        assert payload["messages"][0] == {"role": "system", "content": policy}
        assert wire.headers["Authorization"] == "Bearer fixture-secret"
        if transport == "json_map":
            assert payload["max_tokens"] == 512 and payload["temperature"] == 0
        return httpx.Response(200, headers={"Content-Type": "application/json"},
                              stream=httpx.ByteStream(json.dumps({
                                  "choices": [{"message": {"content": reply}}],
                              }).encode()))

    class Scope:
        def __init__(self, url):
            assert url == "https://agent.example.test/chat"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @contextmanager
        def client(self, **kwargs):
            with httpx.Client(transport=httpx.MockTransport(handle), **kwargs) as client:
                yield client

    monkeypatch.setattr(customer_runner, "ScopedEgressAllowlist", Scope)

    def judge(prompt):
        judged.append(prompt)
        assert "automated AI assistant" in prompt
        return '{"verdict":"pass","score":1.0}'

    def workers(scan_request, secret):
        built = build_live_framework_workers(
            scan_request, secret, evidence_root=tmp_path / "evidence",
            judge_factory=lambda: MockRunner("judge-fixture", judge),
        )
        executors.append(built["promptfoo"])
        return built

    rows = [dict(r, registry_version="e018-registry@1", scope_id=r["risk_or_rubric_id"])
            for r in build_registry() if r["test_id"] == "ai_disclosure"]
    # A different scope must never be dispatched by this review.
    rows.append(dict(rows[0], case_id="unselected", scope_id="OTHER"))
    runtime = ScanRuntime(
        run_scan_fn=lambda *_: pytest.fail("full scan invoked legacy walker"),
        full_scan_registry_rows=rows, full_scan_workers_factory=workers,
    )
    config = {"url": "https://agent.example.test/chat", "transport": transport,
              "model": "self-owned-claude-fixture", "system_prompt": policy,
              "auth": {"type": "bearer"}}
    if transport == "json_map":
        config.update(request_template={
            "model": "self-owned-claude-fixture", "max_tokens": 512, "temperature": 0,
            "messages": [{"role": "system", "content": policy},
                         {"role": "user", "content": "{{prompt}}"}],
        }, response_path="choices.0.message.content")
    scope = disclosure_scope()
    with TestClient(create_app(scan_runtime=runtime)) as client:
        response = client.post("/scanner/v1/scans", headers={"X-Tenant-Id": "self-owned"}, json={
            "tenant_id": "self-owned", "agent_endpoint": config,
            "credential_ref": "fixture", "credential_secret": "fixture-secret",
            "scanner_version": "test", "corpus_version": "test", "seed": 0,
            "corpus_snapshot_hash": "sha256:" + "0" * 64, "scan_tier": "full",
            "max_cost_usd": 0.10,
            "operations_scope": {"mode": "scope_review", "registry_revision": "e018-registry@1",
                                 "scope_ids": scope["scope_ids"],
                                 "case_manifest": scope["requested_case_ids"]},
        })
        assert response.status_code == 202, response.text
        scan_id = response.json()["scan_id"]
        deadline = time.monotonic() + 5
        while True:
            result = runtime.fetch(scan_id)
            if result and result.status not in {"queued", "running"}:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert result.status == "complete", result.full_suite_manifest
        report = client.get("/scan/" + scan_id, headers={"X-Tenant-Id": "self-owned"})
        assert report.status_code == 200
    assert len(received) == len(judged) == 24
    executor = executors[0]
    assert executor.durable_target_ledger.snapshot()["reserved"] == 24
    assert executor.ledger.judge_calls == 24
    row = next(r for r in result.full_suite_manifest["evidence_rows"] if r["probe_id"] == "AD-BYP-05")
    assert row["verdict"] == "pass" and row["score_contribution"] is False
    assert row["target_calls"] == row["judge_calls"] == 1
    evidence = next((tmp_path / "evidence").glob("*/ai_disclosure/AD-BYP-05.json"))
    assert json.loads(evidence.read_text())["responses"] == [reply]
