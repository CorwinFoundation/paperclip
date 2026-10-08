from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.full_scan_executor import EvidenceAttemptRow, FullScanExecutorError, WorkerAttemptResult, _validate_evidence_row
from api.scan_log import ScanLog
from api.scan_handlers import HandlerError, ScanRuntime, scan_status_handler
from harness import egress
from harness.runner import MockRunner, ModelRunner
from harness.customer_runner import CustomerEndpointHTTPError
from scanners.live_probe_orchestrator import ScanRequest, ScanResult
import tools.registered_case_executor as execution
from tools.registered_case_executor import (
    EvidenceStore,
    JudgeResponseError,
    RegisteredCaseExecutor,
    ScanBudget,
    build_registered_case_workers,
    anthropic_haiku_45_cost_usd,
)
from tools.active_test_registry import build_registry


REPO = Path(__file__).resolve().parents[1]


def _row() -> dict[str, object]:
    return {
        "test_id": "p1_1_factuality_golden",
        "case_id": "P1.1-FAC-001",
        "status": "active",
        "pillar": "OPS",
        "category": "factuality",
        "risk_or_rubric_id": "P1.1",
        "source_pack": "rubric/probe_mappings/p1_1_factuality_golden.yaml",
        "runner": "bench_wrapper",
        "version_pin": "p1_1_factuality_golden@0.1.0",
        "production_runner_executable": True,
    }


def _request(scan_id: str) -> ScanRequest:
    return ScanRequest(
        scan_id=scan_id,
        tenant_id="tenant-test",
        agent_endpoint="https://agent.example.test",
        credential_ref="phase1-probe.v1",
        corpus_snapshot_hash="sha256:" + "0" * 64,
        scanner_version="0.4.2",
        corpus_version="execution-registry@2026-07-13",
        seed=42,
        k_replicates=5,
    )


def test_default_full_scan_budget_matches_authorized_ceiling(monkeypatch):
    for name in (
        "ARE_FULL_SCAN_MAX_TARGET_CALLS",
        "ARE_FULL_SCAN_MAX_JUDGE_CALLS",
        "ARE_FULL_SCAN_MAX_JUDGE_COST_USD",
        "ARE_FULL_SCAN_MAX_WALL_CLOCK_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    budget = ScanBudget.from_env()

    assert budget.max_target_calls == 6_000
    assert budget.max_judge_calls == 4_000
    assert budget.max_estimated_judge_cost_usd == 99.99
    assert budget.max_wall_clock_seconds == 6 * 60 * 60


def test_registered_case_calls_target_and_judge_and_writes_evidence(tmp_path):
    target = MockRunner("target", lambda _prompt: "Apollo 11 landed July 20, 1969.")
    judge = MockRunner(
        "judge",
        lambda _prompt: (
            "ANSWER_MATCH: YES\n"
            "PLAUSIBILITY: YES\n"
            "FALSE_SPECIFICITY: NO\n"
            "EVIDENCE_SPAN: July 20, 1969"
        ),
    )
    executor = RegisteredCaseExecutor(
        subject=target,
        judge=judge,
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_estimated_judge_cost_usd=100.0),
    )
    job = SimpleNamespace(
        job_id="job-1",
        agent_id="agent-1",
        seed=42,
    )

    result = executor(_row(), job)

    assert result.status == "complete"
    assert result.verdict == "pass"
    assert result.target_calls == 1
    assert result.judge_calls == 1
    assert result.numeric_result == 1.0
    evidence_path = (
        tmp_path
        / "job-1"
        / "p1_1_factuality_golden"
        / "P1.1-FAC-001.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["payloads"]
    assert evidence["responses"] == ["Apollo 11 landed July 20, 1969."]
    assert evidence["judge"]["answer_match"] == "YES"
    assert evidence["budget"]["case_delta"]["target_calls"] == 1
    assert evidence["payload_sha256"].startswith("sha256:")
    assert evidence["response_sha256"].startswith("sha256:")


def test_materialized_framework_case_records_runtime_identity_and_aliases(tmp_path):
    row = next(
        row
        for row in build_registry()
        if row.get("canonical_runtime_case") is True
        and row["test_id"] == "promptinject"
    )
    target = MockRunner("target", lambda _prompt: "I will ignore the injected instruction.")
    judge = MockRunner("judge", lambda _prompt: '{"verdict":"pass","score":1.0}')
    executor = RegisteredCaseExecutor(
        subject=target,
        judge=judge,
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_estimated_judge_cost_usd=100.0),
    )

    result = executor(
        row,
        SimpleNamespace(job_id="job-materialized", agent_id="agent-1", seed=42),
    )

    assert result.status == "complete"
    assert result.verdict == "pass"
    evidence = json.loads(
        (
            tmp_path
            / "job-materialized"
            / row["test_id"]
            / f"{row['case_id']}.json"
        ).read_text(encoding="utf-8")
    )
    assert evidence["runtime_case_identity"] == row["runtime_case_identity"]
    assert evidence["canonical_runtime_case"] is True
    assert evidence["execution_mode"] == "materialized_framework_compatible_case"
    assert evidence["source_aliases"]
    assert evidence["judge"]["computed_runtime_case_identity"] == row["runtime_case_identity"]


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ('{"verdict":"fail","score":1.0}', "judge_verdict_score_disagreement"),
        ('{"verdict":"pass","score":0.0}', "judge_verdict_score_disagreement"),
        ('{"verdict":"fail","score":9}', "judge_score_out_of_range"),
    ],
)
def test_materialized_judge_rejects_score_verdict_disagreement(raw, reason):
    with pytest.raises(JudgeResponseError, match=reason):
        execution._judge_materialized_text_case(
            MockRunner("judge", lambda _prompt: raw),
            prompts=["prompt"],
            responses=["response"],
            expected={},
            judge_criteria="criteria",
        )


@pytest.mark.parametrize("test_id", ["pyrit", "agentdojo", "halueval"])
@pytest.mark.parametrize("raw", ["", "The response is a pass."])
def test_unparseable_materialized_judge_is_could_not_run(
    tmp_path, test_id, raw
):
    row = next(
        row
        for row in build_registry()
        if row.get("production_runner_executable") is True
        and row["test_id"] == test_id
    )
    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", lambda _prompt: "target response"),
        judge=MockRunner("judge", lambda _prompt: raw),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_estimated_judge_cost_usd=100.0),
    )

    result = executor(
        row,
        SimpleNamespace(job_id=f"job-{test_id}", agent_id="agent-1", seed=42),
    )

    assert result.status == "failed"
    assert result.verdict == "unresolved"
    assert result.numeric_result is None
    assert result.incomplete_reason == "case_execution_error:JudgeResponseError"


def test_vapi_cloudflare_injection_refusal_is_terminal_without_judge(tmp_path):
    row = next(
        row for row in build_registry()
        if row.get("case_id") == "P3.2-ROB-03"
        and row.get("production_runner_executable") is True
    )

    class RefusingVapi(ModelRunner):
        def model_id(self):
            return "vapi:09aea361@api.vapi.ai"

        def complete(self, _prompt):
            raise CustomerEndpointHTTPError(
                403,
                "Forbidden",
                "<title>Attention Required! | Cloudflare</title> Sorry, you have been blocked",
                {"cf-ray": "fixture-ray"},
            )

    executor = RegisteredCaseExecutor(
        subject=RefusingVapi(),
        judge=MockRunner("judge", lambda _: pytest.fail("judge must not run")),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_estimated_judge_cost_usd=100.0),
    )
    result = executor(row, SimpleNamespace(job_id="job-edge-refusal", agent_id="vapi", seed=42))

    assert result.status == "complete"
    assert result.verdict == "edge_refusal"
    assert result.numeric_result is None
    assert result.target_calls == 1
    assert result.judge_calls == 0
    assert result.refusal == {
        "refusing_party": "cloudflare",
        "reason": "The request triggered the target edge security service.",
        "provenance": {"http_status": 403, "http_reason": "Forbidden", "cf_ray": "fixture-ray"},
    }
    evidence = json.loads((tmp_path / "job-edge-refusal" / row["test_id"] / "P3.2-ROB-03.json").read_text())
    assert evidence["judge"] == {
        "cf_ray": "fixture-ray",
        "http_reason": "Forbidden",
        "http_status": 403,
        "judge_called": False,
        "provider": "cloudflare",
        "response_reason": "The request triggered the target edge security service.",
        "type": "target_edge_refusal",
    }


def test_registered_case_budget_failure_is_terminal_and_evidenced(tmp_path):
    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", lambda _prompt: "unused"),
        judge=MockRunner("judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=0),
    )
    result = executor(
        _row(),
        SimpleNamespace(job_id="job-budget", agent_id="agent-1", seed=42),
    )

    assert result.status == "incomplete"
    assert result.incomplete_reason == "budget_exceeded:max_target_calls"
    evidence = json.loads(
        (
            tmp_path
            / "job-budget"
            / "p1_1_factuality_golden"
            / "P1.1-FAC-001.json"
        ).read_text(encoding="utf-8")
    )
    assert evidence["verdict"] == "unresolved"
    assert evidence["error"] == "budget_exceeded:max_target_calls"


def test_tau_without_session_factory_is_gated_not_runner_error(tmp_path):
    row = {
        "test_id": "tau_support_crm",
        "case_id": "tau_support_crm.refund_credit.001",
        "status": "active",
        "framework": "tau-bench",
        "risk_or_rubric_id": "UNSCORED.TAU.SUPPORT_CRM",
        "source_pack": "evals/provisional_tau_support_crm.py",
        "runner": "tau_bench",
        "production_runner_executable": True,
        "scoring_active": False,
        "weight": 0.0,
        "score_contribution": False,
    }
    executor = RegisteredCaseExecutor(
        subject=MockRunner("target", lambda _prompt: "unused"),
        judge=MockRunner("judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_estimated_judge_cost_usd=100.0),
    )

    result = executor(
        row,
        SimpleNamespace(job_id="job-tau-gated", agent_id="agent-1", seed=42),
    )

    assert result.status == "complete"
    assert result.verdict == "not_applicable"
    assert result.incomplete_reason is None
    assert result.target_calls == 0
    assert result.judge_calls == 0
    evidence = json.loads(
        (
            tmp_path
            / "job-tau-gated"
            / "tau_support_crm"
            / "tau_support_crm.refund_credit.001.json"
        ).read_text(encoding="utf-8")
    )
    assert evidence["verdict"] == "not_applicable"
    assert evidence["judge"]["applicability_reason"] == "tau_session_factory_unconfigured"
    assert evidence["budget"]["case_delta"]["target_calls"] == 0


def test_default_judge_opens_only_the_provider_scope(monkeypatch):
    seen = []

    class Scope:
        def __init__(self, endpoint, *, live_bind_scope_id):
            seen.append((endpoint, live_bind_scope_id))

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        execution,
        "OpenRouterRunner",
        lambda **_kwargs: MockRunner("openrouter:test", lambda _prompt: "OK"),
    )
    monkeypatch.setattr(egress, "ScopedEgressAllowlist", Scope)

    judge = execution.default_judge_runner()

    assert judge.complete("reply OK") == "OK"
    assert seen[0][0] == "https://openrouter.ai"
    assert seen[0][1].startswith("full-scan-judge-")


def test_anthropic_prefix_selects_direct_provider_and_cache(monkeypatch):
    seen, captured = [], {}
    class Scope:
        def __init__(self, endpoint, *, live_bind_scope_id): seen.append(endpoint)
        def __enter__(self): return self
        def __exit__(self, *_exc): return None
    monkeypatch.setenv("ARE_FULL_SCAN_JUDGE_MODEL", "anthropic/claude-haiku-4.5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "direct-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fallback-key")
    monkeypatch.setattr(execution, "AnthropicRunner", lambda **kwargs: captured.update(kwargs) or MockRunner("direct", lambda _p: "OK"))
    monkeypatch.setattr(egress, "ScopedEgressAllowlist", Scope)
    assert execution.default_judge_runner().complete("judge") == "OK"
    assert seen == ["https://api.anthropic.com"]
    assert captured == {"model": "claude-haiku-4-5-20251001", "max_tokens": 256, "temperature": 0.0, "cache_prompt": True, "batch": False}


def test_explicit_anthropic_provider_never_falls_through_to_openrouter(monkeypatch):
    monkeypatch.setenv("ARE_FULL_SCAN_JUDGE_PROVIDER", "anthropic")
    monkeypatch.setenv("ARE_FULL_SCAN_JUDGE_MODEL", "anthropic/claude-haiku-4.5")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-win")
    monkeypatch.setattr(
        execution,
        "OpenRouterRunner",
        lambda **_kwargs: pytest.fail("explicit Anthropic must not fall through"),
    )

    assert execution.default_judge_runner().model_id() == "judge:unavailable"


def test_anthropic_cost_uses_all_usage_buckets_and_batch_discount():
    usage = {
        "input_tokens": 1_000_000,
        "cache_creation_input_tokens": 1_000_000,
        "cache_read_input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
    }
    assert anthropic_haiku_45_cost_usd(usage, batch=False) == 7.35
    assert anthropic_haiku_45_cost_usd(usage, batch=True) == 3.675


def test_budget_ledger_reconciles_to_anthropic_usage(tmp_path):
    class UsageRunner(MockRunner):
        _batch = True
        last_usage = {
            "input_tokens": 100,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 300,
            "output_tokens": 40,
        }
    runner = UsageRunner("usage", lambda _prompt: "OK")
    ledger = execution.ScanBudgetLedger(ScanBudget(max_estimated_judge_cost_usd=1))
    budgeted = execution._BudgetedRunner(runner, ledger, role="judge")
    assert budgeted.complete("rubric") == "OK"
    assert ledger.snapshot()["estimated_judge_cost_usd"] == round(
        anthropic_haiku_45_cost_usd(runner.last_usage, batch=True), 6
    )


def test_pinned_anthropic_sdk_exposes_message_batches():
    import anthropic

    client = anthropic.Anthropic(api_key="test-key")
    assert hasattr(client.messages, "batches")


def test_anthropic_batch_sends_cached_block_and_captures_usage(monkeypatch):
    captured = {}
    usage = SimpleNamespace(
        input_tokens=1, cache_creation_input_tokens=2,
        cache_read_input_tokens=3, output_tokens=4,
    )
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="PASS")], usage=usage
    )
    result = SimpleNamespace(result=SimpleNamespace(type="succeeded", message=message))
    batches = SimpleNamespace(
        create=lambda **kwargs: captured.update(kwargs) or SimpleNamespace(id="b1", processing_status="ended"),
        results=lambda _id: iter([result]),
    )
    fake = SimpleNamespace(
        Anthropic=lambda: SimpleNamespace(messages=SimpleNamespace(batches=batches)),
        BadRequestError=type("BadRequestError", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    runner = execution.AnthropicRunner(
        "claude-haiku-4-5-20251001", cache_prompt=True, batch=True,
        batch_poll_seconds=0,
    )
    assert runner.complete("rubric bytes\nRESPONSE: row bytes") == "PASS"
    params = captured["requests"][0]["params"]
    blocks = params["messages"][0]["content"]
    assert blocks[0] == {
        "type": "text", "text": "rubric bytes",
        "cache_control": {"type": "ephemeral"},
    }
    assert blocks[1] == {"type": "text", "text": "\nRESPONSE: row bytes"}
    assert runner.last_usage == {
        "input_tokens": 1, "cache_creation_input_tokens": 2,
        "cache_read_input_tokens": 3, "output_tokens": 4,
    }


def test_anthropic_cache_breakpoint_precedes_materialized_responses(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    runner = execution.AnthropicRunner("claude-haiku-4-5-20251001", cache_prompt=True)
    blocks = runner._message_content("rubric and contract\nResponses JSON:\n[\"row\"]\n")
    assert blocks[0] == {
        "type": "text", "text": "rubric and contract",
        "cache_control": {"type": "ephemeral"},
    }
    assert blocks[1]["text"].startswith("\nResponses JSON:")


def test_missing_anthropic_key_falls_back_to_openrouter(monkeypatch):
    captured = {}
    monkeypatch.setenv("ARE_FULL_SCAN_JUDGE_MODEL", "anthropic/claude-haiku-4.5")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fallback-key")
    monkeypatch.setattr(execution, "OpenRouterRunner", lambda **kwargs: captured.update(kwargs) or MockRunner("fallback", lambda _p: "OK"))
    assert execution.default_judge_runner().complete("judge") == "OK"
    assert captured["model"] == "anthropic/claude-haiku-4.5"


def test_worker_factory_preserves_legacy_request_without_tenant(tmp_path):
    request = SimpleNamespace(agent_endpoint="https://agent.example.test")
    workers = build_registered_case_workers(
        request,
        "secret",
        evidence_root=tmp_path,
        subject_factory=lambda _endpoint, _secret: MockRunner(
            "target", lambda _prompt: "Apollo 11 landed July 20, 1969."
        ),
        judge_factory=lambda: MockRunner(
            "judge",
            lambda _prompt: (
                "ANSWER_MATCH: YES\nPLAUSIBILITY: YES\n"
                "FALSE_SPECIFICITY: NO\nEVIDENCE_SPAN: July 20, 1969"
            ),
        ),
    )
    executor = next(iter(workers.values()))
    assert executor.tenant_id is None
    result = executor(
        _row(),
        SimpleNamespace(job_id="legacy-203", agent_id="agent", seed=42),
    )
    assert result.status == "complete"
    assert result.verdict == "pass"


def test_worker_factory_propagates_genuine_request_tenant(tmp_path):
    request = SimpleNamespace(
        agent_endpoint="https://agent.example.test",
        tenant_id="tenant-real",
    )
    workers = build_registered_case_workers(
        request,
        "secret",
        evidence_root=tmp_path,
        subject_factory=lambda _endpoint, _secret: MockRunner("target", lambda _prompt: "ok"),
        judge_factory=lambda: MockRunner("judge", lambda _prompt: "ok"),
    )
    assert next(iter(workers.values())).tenant_id == "tenant-real"


def test_worker_factory_passes_agent_id_to_production_subject(
    tmp_path, monkeypatch
):
    captured = {}

    def production_subject(endpoint, secret, agent_id):
        captured.update(endpoint=endpoint, secret=secret, agent_id=agent_id)
        return MockRunner("target", lambda _prompt: "ok")

    monkeypatch.setattr(
        "harness.customer_runner.customer_agent_http_runner_factory",
        production_subject,
    )
    request = SimpleNamespace(
        agent_endpoint="https://api.vapi.ai",
        tenant_id="tenant-vapi",
        agent_id="09aea361-04b3-4a62-bcb0-2f2582f11ec8",
    )

    build_registered_case_workers(
        request,
        "vapi-key-fixture",
        evidence_root=tmp_path,
        judge_factory=lambda: MockRunner("judge", lambda _prompt: "ok"),
    )

    assert captured == {
        "endpoint": "https://api.vapi.ai",
        "secret": "vapi-key-fixture",
        "agent_id": "09aea361-04b3-4a62-bcb0-2f2582f11ec8",
    }


def test_full_registry_kickoff_is_async_and_bypasses_legacy_walker(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "registered-execution-test-key")
    legacy_called = []

    def legacy(_request, _secret):
        legacy_called.append(True)
        raise AssertionError("full registry must not invoke legacy four-prompt walker")

    def workers_factory(_request, _secret):
        return {
            "inspect": lambda _row, _job: WorkerAttemptResult(
                status="complete",
                evidence_ref="evidence://test/P1.1-FAC-001.json",
                verdict="pass",
                target_calls=1,
                judge_calls=1,
                estimated_judge_cost_usd=0.01,
                numeric_result=1.0,
            )
        }

    runtime = ScanRuntime(
        run_scan_fn=legacy,
        full_scan_registry_rows=[_row()],
        full_scan_workers_factory=workers_factory,
    )
    request = _request("scan-async-1")

    queued = runtime.kickoff(request, "secret")

    assert queued.status == "queued"
    deadline = time.monotonic() + 3
    completed = runtime.fetch(request.scan_id)
    while completed is not None and completed.status in {"queued", "running"}:
        assert time.monotonic() < deadline
        time.sleep(0.01)
        completed = runtime.fetch(request.scan_id)

    assert completed is not None
    assert completed.status == "complete"
    assert completed.rows_emitted == 1
    assert completed.full_suite_manifest["verdict_counts"]["pass"] == 1
    assert completed.full_suite_manifest["budget"]["estimated_judge_cost_usd"] == 0.01
    evidence_row = completed.full_suite_manifest["evidence_rows"][0]
    assert evidence_row["numeric_result"] == 1.0
    assert evidence_row["acquisition_basis"] == "target-measured"
    assert evidence_row["underwriting_relevance"] == "direct"
    assert evidence_row["score_contribution"] is False
    assert evidence_row["disposition"] == "report-only"
    assert evidence_row["derived_result_id"]
    assert legacy_called == []


def test_garak_rate_and_sample_counts_reach_full_manifest(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "registered-execution-test-key")
    row = {
        **_row(), "test_id": "garak_beaaa152", "case_id": "garak.test.Probe",
        "runner": "garak_wrapper",
    }
    runtime = ScanRuntime(
        run_scan_fn=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        full_scan_registry_rows=[row],
        full_scan_workers_factory=lambda *_args: {
            "garak": lambda *_worker_args: WorkerAttemptResult(
                status="complete",
                evidence_ref="evidence://fixture/garak.json",
                target_calls=48,
                numeric_result=0.25,
                hit_count=12,
                sample_size=48,
            )
        },
    )
    request = _request("scan-garak-rate")
    runtime.kickoff(request, "secret")
    deadline = time.monotonic() + 3
    completed = runtime.fetch(request.scan_id)
    while completed is not None and completed.status in {"queued", "running"}:
        assert time.monotonic() < deadline
        time.sleep(0.01)
        completed = runtime.fetch(request.scan_id)

    evidence_row = completed.full_suite_manifest["evidence_rows"][0]
    assert evidence_row["numeric_result"] == 0.25
    assert evidence_row["numeric_result"] == evidence_row["hit_count"] / evidence_row["sample_size"]
    assert evidence_row["score_contribution"] is False
    assert evidence_row["disposition"] == "report-only"


def test_halueval_attempt_stays_category_proxy_and_report_only(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "registered-execution-test-key")
    row = {**_row(), "test_id": "halueval", "case_id": "halueval-1"}
    runtime = ScanRuntime(
        run_scan_fn=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        full_scan_registry_rows=[row],
        full_scan_workers_factory=lambda *_args: {
            "promptfoo": lambda *_worker_args: WorkerAttemptResult(
                status="complete",
                evidence_ref="evidence://test/halueval-1.json",
                verdict="pass",
                target_calls=1,
                numeric_result=0.75,
            )
        },
    )
    request = _request("scan-halueval-provenance")
    runtime.kickoff(request, "secret")
    deadline = time.monotonic() + 3
    completed = runtime.fetch(request.scan_id)
    while completed is not None and completed.status in {"queued", "running"}:
        assert time.monotonic() < deadline
        time.sleep(0.01)
        completed = runtime.fetch(request.scan_id)

    evidence_row = completed.full_suite_manifest["evidence_rows"][0]
    assert evidence_row["underwriting_relevance"] == "category-proxy"
    assert evidence_row["score_contribution"] is False
    assert evidence_row["disposition"] == "report-only"


def test_complete_without_attributable_target_call_is_not_measured(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "registered-execution-test-key")
    runtime = ScanRuntime(
        run_scan_fn=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        full_scan_registry_rows=[_row()],
        full_scan_workers_factory=lambda *_args: {
            "inspect": lambda *_worker_args: WorkerAttemptResult(
                status="complete",
                evidence_ref="evidence://fixture/judge-only.json",
                verdict="pass",
                target_calls=0,
                judge_calls=1,
            )
        },
    )
    request = _request("scan-complete-without-target")
    runtime.kickoff(request, "secret")
    deadline = time.monotonic() + 3
    completed = runtime.fetch(request.scan_id)
    while completed is not None and completed.status in {"queued", "running"}:
        assert time.monotonic() < deadline
        time.sleep(0.01)
        completed = runtime.fetch(request.scan_id)

    evidence_row = completed.full_suite_manifest["evidence_rows"][0]
    assert evidence_row["status"] == "complete"
    assert evidence_row["target_calls"] == 0
    assert evidence_row["acquisition_basis"] == "missing"
    assert evidence_row["score_contribution"] is False
    assert evidence_row["disposition"] == "report-only"


def test_attempt_manifest_fields_are_tenant_isolated_and_redacted(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "registered-execution-test-key")
    raw_prompt = "PROPRIETARY PROMPT tenant alpha"
    raw_response = "PROPRIETARY RESPONSE tenant alpha"
    credential = "credential-secret-tenant-alpha"
    runtime = ScanRuntime(
        run_scan_fn=lambda *_args: (_ for _ in ()).throw(AssertionError()),
        full_scan_registry_rows=[_row()],
        full_scan_workers_factory=lambda *_args: {
            "inspect": lambda *_worker_args: WorkerAttemptResult(
                status="complete",
                evidence_ref="evidence://tenant-alpha/redacted.json",
                verdict="pass",
                target_calls=1,
                judge_calls=1,
                numeric_result=1.0,
            )
        },
    )
    request = _request("scan-tenant-isolation")
    runtime.kickoff(request, credential)
    deadline = time.monotonic() + 3
    completed = runtime.fetch(request.scan_id)
    while completed is not None and completed.status in {"queued", "running"}:
        assert time.monotonic() < deadline
        time.sleep(0.01)
        completed = runtime.fetch(request.scan_id)

    status, _owner_payload = scan_status_handler(
        request.scan_id,
        runtime=runtime,
        authenticated_tenant_id="tenant-test",
    )
    assert status == 200
    with pytest.raises(HandlerError, match="scan_not_found"):
        scan_status_handler(
            request.scan_id,
            runtime=runtime,
            authenticated_tenant_id="tenant-other",
        )

    manifest_json = json.dumps(completed.full_suite_manifest, sort_keys=True)
    for prohibited in (
        raw_prompt,
        raw_response,
        credential,
        "https://agent.example.test",
        "phase1-probe.v1",
    ):
        assert prohibited not in manifest_json
    row = completed.full_suite_manifest["evidence_rows"][0]
    assert set(row) >= {
        "acquisition_basis",
        "underwriting_relevance",
        "score_contribution",
        "disposition",
        "case_version",
        "derived_result_id",
        "derived_result_version",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("acquisition_basis", "invented"),
        ("underwriting_relevance", "invented"),
        ("disposition", "score-bearing"),
    ],
)
def test_registered_case_provenance_enums_fail_closed(field, value):
    row = EvidenceAttemptRow(
        probe_id="case-1",
        rubric_point_id="P1.1",
        status="complete",
        framework="inspect@test",
        corpus_version="corpus@test",
        model_version="model@test",
        seed=42,
        evidence_ref="evidence://test/redacted.json",
        target_calls=1,
    )
    with pytest.raises(FullScanExecutorError):
        _validate_evidence_row(row.__class__(**{**row.__dict__, field: value}))


def test_registry_only_full_scan_persists_score_envelope_without_dim_results(monkeypatch, tmp_path):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "registered-execution-test-key")
    scan_log = ScanLog(tmp_path / "scan-log.sqlite3")

    def workers_factory(_request, _secret):
        return {
            "inspect": lambda _row, _job: WorkerAttemptResult(
                status="complete",
                evidence_ref="evidence://test/P1.1-FAC-001.json",
                verdict="pass",
            )
        }

    def envelope_factory(result, agent_id, run_timestamp):
        assert result.full_suite_manifest is not None
        assert result.dim_results == ()
        return {
            "agent_id": agent_id,
            "are_total": 90,
            "band": "A",
            "confidence_below_adjudication_threshold": False,
            "corpus_version": result.corpus_version,
            "evidence_refs": [result.full_suite_manifest["manifest_evidence_ref"]],
            "model_versions": {"primary": "fixture-agent-v0", "judges": []},
            "partial_completion": False,
            "prior_scan_refs": [],
            "purpose": "pre_bind",
            "rubric_version": "rubric@fixture",
            "run_timestamp": run_timestamp,
            "scanner_version": result.scanner_version,
            "seed": 42,
            "subscores": {"OPS": 90},
        }

    runtime = ScanRuntime(
        run_scan_fn=lambda request, _secret: ScanResult(
            scan_id=request.scan_id,
            status="complete",
            scanner_version=request.scanner_version,
            corpus_version=request.corpus_version,
            rows_emitted=0,
            k_replicates=request.k_replicates,
            dim_results=(),
        ),
        scan_log=scan_log,
        full_scan_registry_rows=[_row()],
        full_scan_workers_factory=workers_factory,
        run_timestamp_factory=lambda: "2026-07-22T12:00:00Z",
        score_envelope_factory=envelope_factory,
    )
    request = _request("scan-registry-persist")

    runtime.kickoff(request, "secret", agent_id="agent-1")
    deadline = time.monotonic() + 3
    completed = runtime.fetch(request.scan_id)
    while completed is not None and completed.status in {"queued", "running"}:
        assert time.monotonic() < deadline
        time.sleep(0.01)
        completed = runtime.fetch(request.scan_id)

    rows = scan_log.list_for_agent("agent-1", tenant_id=request.tenant_id)
    assert len(rows) == 1
    assert rows[0]["scan_id"] == "scan-registry-persist"
    assert rows[0]["score_json"]["evidence_refs"] == [
        completed.full_suite_manifest["manifest_evidence_ref"]
    ]


def test_running_full_scan_does_not_expire_from_status_store():
    now = [100.0]
    release_worker = threading.Event()

    def worker(_row, _job):
        assert release_worker.wait(timeout=2)
        return WorkerAttemptResult(
            status="complete",
            evidence_ref="evidence://test/P1.1-FAC-001.json",
            verdict="pass",
        )

    runtime = ScanRuntime(
        run_scan_fn=lambda _request, _secret: (_ for _ in ()).throw(
            AssertionError("legacy walker must not run")
        ),
        full_scan_registry_rows=[_row()],
        full_scan_workers_factory=lambda _request, _secret: {"inspect": worker},
        in_memory_state_ttl_seconds=10,
        monotonic_clock=lambda: now[0],
    )
    request = _request("scan-long-running")
    runtime.kickoff(request, "secret")

    now[0] = 111.0
    active = runtime.fetch(request.scan_id)
    assert active is not None
    assert active.status in {"queued", "running"}

    release_worker.set()
    deadline = time.monotonic() + 2
    while runtime.fetch(request.scan_id).status in {"queued", "running"}:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    now[0] = 122.0
    assert runtime.fetch(request.scan_id) is None
