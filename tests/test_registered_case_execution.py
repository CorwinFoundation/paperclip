from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.full_scan_executor import EvidenceAttemptRow, FullScanExecutorError, WorkerAttemptResult, _validate_evidence_row
from api.scan_log import ScanLog
from api.scan_handlers import HandlerError, ScanRuntime, scan_status_handler
from harness import egress
from harness.runner import MockRunner
from scanners.live_probe_orchestrator import ScanRequest, ScanResult
import tools.registered_case_executor as execution
from tools.registered_case_executor import (
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
    build_registered_case_workers,
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

    rows = scan_log.list_for_agent("agent-1")
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
