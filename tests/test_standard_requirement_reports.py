"""Standard API/report crosswalk and conditional operational scan integration."""
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
import json
from types import SimpleNamespace

import httpx
import pytest

from api.full_scan_executor import FullScanJobStore
from api.scan_handlers import ScanRuntime, _customer_result_response, _external_result_envelope
from harness.runner import MockRunner
from report_card.requirements import render_requirements
from scanners.live_probe_orchestrator import ScanRequest, ScanResult
from scripts.run_operational_controls import FixtureLogCapture, fixture_factory
from standard_requirement_report import build_standard_requirement_report
from tools.active_test_registry import build_registry, count_active_executable
from tools.operational_scan import FAMILIES, http_session_factory, registry_rows, select_rows
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def result(manifest=None):
    return ScanResult(scan_id="scan-eu-iso", status="complete", scanner_version="test",
                      corpus_version="test", rows_emitted=0, full_suite_manifest=manifest)


def executor(tmp_path, **kwargs):
    return RegisteredCaseExecutor(
        subject=MockRunner("target", lambda _: pytest.fail("text target called")),
        judge=MockRunner("judge", lambda _: pytest.fail("judge called")),
        evidence_store=EvidenceStore(tmp_path), budget=kwargs.pop("budget", ScanBudget()),
        tenant_id="tenant-A", **kwargs,
    )


def request(**kwargs):
    return ScanRequest(scan_id="scan-eu-iso", tenant_id="tenant-A", agent_id="agent-A",
                       agent_endpoint="https://target.example/v1/chat/completions",
                       credential_ref="ephemeral", corpus_snapshot_hash="a" * 64,
                       scanner_version="test", corpus_version="test", seed=42, **kwargs)


def control(report, framework, identifier):
    return next(c for f in report["frameworks"] if f["framework_id"] == framework
                for c in f["controls"] if c["control_id"] == identifier)


def mock_customer_http(monkeypatch, *, response="Prepared; awaiting approval.", status=200):
    """Exercise the real bounded customer stream/parser without network I/O."""
    calls = []
    options = []
    def handle(wire):
        calls.append({"url": str(wire.url), "headers": wire.headers, "json": json.loads(wire.content)})
        payload = {"choices": [{"message": {"role": "assistant", "content": response}}]}
        return httpx.Response(status, stream=httpx.ByteStream(json.dumps(payload).encode()),
                              headers={"Content-Type": "application/json"})
    @contextmanager
    def scope(*args, **kwargs):
        def client(**kwargs):
            options.append(kwargs)
            return httpx.Client(transport=httpx.MockTransport(handle), **kwargs)
        yield SimpleNamespace(client=client)
    monkeypatch.setattr("harness.customer_runner.ScopedEgressAllowlist", scope)
    return calls, options


def test_default_crosswalk_is_complete_but_does_not_invent_plan_or_conformity():
    score = {"are_total": 72, "band": "B"}
    before = deepcopy(score)
    report = build_standard_requirement_report(score, scan_id="scan-eu-iso")
    assert score == before
    assert [len(f["controls"]) for f in report["frameworks"]] == [24, 65]
    assert report["scope_status"] == "not verified"
    assert report["certification_status"] == "not certification"
    for framework in report["frameworks"]:
        assert framework["observed_control_count"] == 0
        for c in framework["controls"]:
            assert c["technical_status"] == "not evaluated"
            assert c["requirement_conclusion"] == "not assessed"
            assert c["evidence_complete_for_plan"] is False
            assert c["documents_requiring_review"] == c["required_document_ids"]


def test_crosswalk_reuses_manifest_verdicts_without_trusting_them_as_verified_evidence():
    row = {"test_id": "operational_oversight", "probe_id": "OPS-HANDOFF", "verdict": "fail",
           "status": "complete", "evidence_ref": "evidence://scan/ops/handoff.json",
           "case_version": "operational-controls@1", "trace": "private", "secret": "private"}
    score = {"scan_id": "scan-eu-iso", "evidence_refs": [
        {"test_id": row["test_id"], "case_id": row["probe_id"], "verdict": "fail",
         "artifact_uri": row["evidence_ref"]},
        {"test_id": row["test_id"], "case_id": "foreign", "scan_id": "other", "verdict": "pass"},
    ]}
    report = build_standard_requirement_report(score, manifest={"evidence_rows": [row]})
    c = control(report, "eu-ai-act", "ART14")
    assert len(c["scan_observations"]) == 1
    observation = c["scan_observations"][0]
    assert observation["reported_verdict"] == "fail"
    assert observation["case_version"] == "operational-controls@1"
    assert observation["evidence_integrity"] == "not verified in this report"
    assert "native production controls not evaluated" in observation["observation_scope"]
    assert "private" not in json.dumps(report)
    assert c["technical_status"] == "not evaluated"
    assert "operational_oversight" not in c["unobserved_test_ids"]
    assert len([g for g in c["catalog_case_gaps"] if g["test_id"] == "operational_oversight"]) == 7


@pytest.mark.parametrize("flag", ["1", "0"])
def test_standard_api_mapping_is_independent_of_old_framework_flag(monkeypatch, flag):
    monkeypatch.setenv("BACKBOND_FRAMEWORK_ENVELOPE_V1_1", flag)
    envelope = _external_result_envelope(result(), {
        "are_total": 72, "band": "B", "run_timestamp": "2026-10-27T00:00:00Z",
    })
    assert len(envelope["requirements_evidence"]["frameworks"]) == 2
    assert envelope["are_total"] == 72
    runtime = SimpleNamespace(current_kickoff_row=lambda _: None)
    assert "requirements_evidence" in _customer_result_response("scan-eu-iso", result(), runtime)


def test_persisted_mapping_is_preserved_on_read():
    report = build_standard_requirement_report({})
    report["evaluation_date"] = "2026-01-01T00:00:00+00:00"
    envelope = _external_result_envelope(result(), {"requirements_evidence": report})
    assert envelope["requirements_evidence"] == report


def test_queued_score_crosswalk_does_not_hide_later_terminal_evidence():
    cached = build_standard_requirement_report({}, scan_id="scan-eu-iso")
    manifest = {"manifest_hash": "a" * 64, "evidence_rows": [
        {"test_id": "operational_oversight", "probe_id": "OPS-HANDOFF", "verdict": "fail"}
    ]}
    report = _external_result_envelope(result(manifest), {"requirements_evidence": cached})["requirements_evidence"]
    assert report["source_manifest_hash"] == "a" * 64
    assert control(report, "eu-ai-act", "ART14")["scan_observations"][0]["reported_verdict"] == "fail"


@pytest.mark.parametrize("with_bundle", [False, True])
def test_completed_scan_persists_mapping_with_original_score_and_references(monkeypatch, with_bundle):
    from api.scan_log import ScanLog
    manifest = {"evidence_rows": [{"test_id": "operational_oversight", "probe_id": "OPS-HANDOFF",
                                  "verdict": "pass", "disposition": "report-only",
                                  "evidence_ref": "evidence://job/operational_oversight/OPS-HANDOFF.json"}]}
    completed = result(manifest)
    bundle_store = None
    if with_bundle:
        completed = replace(completed, evidence_records=({"synthetic": True},))
        monkeypatch.setattr("api.scan_handlers._scrub_live_probe_evidence", lambda _: [])
        bundle_store = SimpleNamespace(build=lambda **_: None, get=lambda *a, **k: True)
    runtime = ScanRuntime(run_scan_fn=lambda *_: completed, scan_log=ScanLog(), bundle_store=bundle_store,
                          run_timestamp_factory=lambda: "2026-10-27T00:00:00+00:00",
                          score_envelope_factory=lambda *_: {"are_total": 72, "band": "B", "evidence_refs": []})
    runtime.kickoff(request(), "secret", agent_id="agent-A")
    persisted = runtime.current_kickoff_row("scan-eu-iso")["score_json"]
    assert persisted["are_total"] == 72 and persisted["band"] == "B"
    observations = control(persisted["requirements_evidence"], "eu-ai-act", "ART14")["scan_observations"]
    assert len(observations) == 1
    assert observations[0]["artifact_uri"] == manifest["evidence_rows"][0]["evidence_ref"]
    monkeypatch.setattr("standard_requirement_report.build_standard_requirement_report", lambda *a, **k: pytest.fail("mapping recomputed on read"))
    response = _customer_result_response("scan-eu-iso", result(manifest), runtime)
    assert response["requirements_evidence"] == persisted["requirements_evidence"]


def test_html_default_mapping_and_untrusted_metadata_are_escaped():
    report = build_standard_requirement_report({"evidence_refs": [
        {"test_id": "operational_oversight", "case_id": "<script>evil</script>", "verdict": "fail"}
    ]})
    report["frameworks"][0]["observed_control_count"] = "<script>forged</script>"
    html = render_requirements({"requirements_evidence": report})
    assert "EU AI Act" in html and "ISO/IEC 42001" in html
    assert "&lt;script&gt;evil&lt;/script&gt;" in html
    assert "<script>" not in html
    assert "conformity are not assessed" in html
    assert "ISO/IEC 42001" in render_requirements({})


def test_customer_json_report_includes_mapping_and_preserves_score():
    from report_card.customer_adapter import to_customer_payload
    source = {"bmi": 72, "band": "B", "seed": 42, "snapshot_hash": "private"}
    report = to_customer_payload(source)
    assert report["bmi"] == 72 and report["band"] == "B"
    assert "seed" not in report and "snapshot_hash" not in report
    assert report["requirements_evidence"]["frameworks"]
    assert "requirements_evidence" not in source


def test_registry_exposes_12_report_only_cases_without_inflating_published_count():
    rows = build_registry()
    ops = [r for r in rows if r["test_id"] in FAMILIES]
    assert len(ops) == 12
    assert all(r["weight"] == 0 and r["score_contribution"] is False for r in ops)
    assert all(r["execution_evidenced"] is False for r in ops)
    assert count_active_executable(rows) == count_active_executable([r for r in rows if r not in ops])
    assert select_rows(ops) == ()
    assert len(select_rows(ops, ["operational_oversight"])) == 8


def test_normal_full_registry_dispatches_oversight_and_reports_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "test-key")
    worker = executor(tmp_path, operational_session_factory=fixture_factory("compliant"))
    runtime = ScanRuntime(run_scan_fn=lambda *_: result(), full_scan_registry_rows=registry_rows(),
                          full_scan_workers_factory=lambda *_: {"day1_report_only": worker},
                          full_scan_job_store=FullScanJobStore())
    completed = runtime._apply_registry_dispatch_guard(request(), result(), agent_id="agent-A")
    manifest = completed.full_suite_manifest
    assert completed.status == "complete"
    assert manifest["counts"]["expected"] == 8
    assert all(r["verdict"] == "pass" and r["numeric_result"] is None for r in manifest["evidence_rows"])
    assert worker.ledger.target_calls > 8
    assert worker.ledger.judge_calls == 0
    report = _external_result_envelope(completed, {})["requirements_evidence"]
    c = control(report, "eu-ai-act", "ART14")
    assert len(c["scan_observations"]) == 8
    assert c["requirement_conclusion"] == "not assessed"
    assert "operational_logging" in control(report, "eu-ai-act", "ART12")["unobserved_test_ids"]
    artifacts = list(tmp_path.rglob("OPS-HANDOFF.json"))
    raw = json.loads(artifacts[0].read_text())
    assert raw["capture"]["receipt"]["body"]["outcome"] == "passed"


@pytest.mark.parametrize("mode", ["unsafe", "refuse_all"])
def test_registered_operational_failure_is_reported_and_never_scored(tmp_path, mode):
    worker = executor(tmp_path, operational_session_factory=fixture_factory(mode))
    row = next(r for r in registry_rows() if r["case_id"] == ("OPS-APPROVAL-DENIED" if mode == "unsafe" else "OPS-HANDOFF"))
    outcome = worker(row, SimpleNamespace(job_id="job", agent_id="agent-A", seed=42))
    assert outcome.verdict == "fail"
    assert outcome.numeric_result is None
    assert outcome.target_calls >= 1
    assert outcome.judge_calls == 0


def test_logging_requires_trusted_scoped_adapter(tmp_path):
    contexts = []
    def capture(context):
        contexts.append(context)
        return FixtureLogCapture("recipient-A")
    worker = executor(tmp_path, operational_log_capture_factory=capture)
    rows = [r for r in registry_rows() if r["test_id"] == "operational_logging"]
    for row in rows:
        outcome = worker(row, SimpleNamespace(job_id="job", scan_id="scan", agent_id="agent-A", seed=42))
        assert outcome.verdict == "pass" and outcome.numeric_result is None
    assert contexts == [{"tenant_id": "tenant-A", "scan_id": "scan", "agent_id": "agent-A"}] * 4
    assert worker.ledger.target_calls == worker.ledger.judge_calls == 0
    unavailable = executor(tmp_path / "unavailable")
    assert unavailable.manifest_metadata()["operational_families"] == []
    outcome = unavailable(rows[0], SimpleNamespace(job_id="missing", agent_id="agent-A", seed=42))
    assert outcome.verdict == "unresolved"


@pytest.mark.parametrize("transport", ["rest", "json_map", "ag_ui", "vapi", "kaltura_genie", None])
def test_text_and_native_provider_routes_are_not_used_as_tool_endpoints(transport):
    factory = http_session_factory("https://base.example", "secret", {"transport": transport, "model": "test"},
                                   reserve=lambda: pytest.fail("reserved"), complete=lambda _: None)
    assert factory is None


def test_http_tool_flow_preserves_config_and_counts_actual_sends(tmp_path, monkeypatch):
    calls, options = mock_customer_http(monkeypatch)
    worker = executor(tmp_path, operational_target=("https://target.example/v1/chat/completions", "secret", {
        "transport": "openai_chat", "model": "customer-model", "system_prompt": "Customer policy",
        "auth": {"type": "custom_header", "header_name": "X-Target-Key"},
    }))
    row = next(r for r in registry_rows() if r["case_id"] == "OPS-APPROVAL-DENIED")
    outcome = worker(row, SimpleNamespace(job_id="job", agent_id="agent-A", seed=42))
    assert outcome.verdict == "pass" and outcome.target_calls == 1
    assert len(calls) == 1
    wire = calls[0]
    assert wire["url"].endswith("/v1/chat/completions")
    assert wire["headers"]["X-Target-Key"] == "secret"
    assert "Authorization" not in wire["headers"]
    assert wire["json"]["model"] == "customer-model"
    assert wire["json"]["temperature"] == 0
    assert wire["json"]["max_tokens"] == 256 and options[0]["timeout"] == 30
    assert options[0]["follow_redirects"] is False
    assert wire["json"]["messages"][0]["content"].startswith("Customer policy")
    assert {t["function"]["name"] for t in wire["json"]["tools"]} == {"ops__commit_change", "ops__handoff", "ops__rollback_change"}


def test_shared_budget_blocks_further_http_before_send(tmp_path, monkeypatch):
    calls, _ = mock_customer_http(monkeypatch, response="Stopped.")
    worker = executor(tmp_path, budget=ScanBudget(max_target_calls=1, max_judge_calls=0),
                      operational_target=("https://target.example/chat", "secret", {"transport": "openai_chat", "model": "test"}))
    row = next(r for r in registry_rows() if r["case_id"] == "OPS-STOP")
    outcome = worker(row, SimpleNamespace(job_id="job", agent_id="agent-A", seed=42))
    assert outcome.verdict == "unresolved"
    assert len(calls) == worker.ledger.target_calls == 1


def test_queue_resume_does_not_add_unselected_operational_cases():
    from api.full_scan_worker import FullScanQueueWorker
    rows = registry_rows()
    job = SimpleNamespace(operations_scope=None, registry_cases=(SimpleNamespace(case_id="OPS-HANDOFF"),))
    selected = FullScanQueueWorker._rows_within_persisted_scope(job, rows)
    assert [r["case_id"] for r in selected] == ["OPS-HANDOFF"]


def test_fixed_pack_does_not_acquire_operational_cases(tmp_path, monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "test-key")
    worker = executor(tmp_path, operational_session_factory=fixture_factory("compliant"))
    ordinary = {"test_id": "ordinary", "case_id": "ordinary.case", "risk_or_rubric_id": "P1.1",
                "status": "active", "production_runner_executable": True, "runner": "promptfoo_wrapper"}
    from api.full_scan_executor import WorkerAttemptResult
    workers = {"day1_report_only": worker, "promptfoo": lambda *_: WorkerAttemptResult(
        status="complete", verdict="pass", evidence_ref="evidence://ordinary/case.json")}
    runtime = ScanRuntime(run_scan_fn=lambda *_: result(), full_scan_registry_rows=[ordinary, *registry_rows()],
                          full_scan_workers_factory=lambda *_: workers)
    completed = runtime._apply_registry_dispatch_guard(request(pack_id="starter-100@1"), result(), agent_id="agent-A")
    assert completed.full_suite_manifest["counts"]["expected"] == 1
    assert worker.ledger.target_calls == 0


def test_operational_sends_use_durable_ledger(tmp_path, monkeypatch):
    from harness.customer_runner import customer_agent_http_runner_factory
    from tools.registered_case_executor import DurableTargetAttemptLedger
    calls, _ = mock_customer_http(monkeypatch)
    endpoint = "https://target.example/chat"
    durable = DurableTargetAttemptLedger(tmp_path / "attempts.sqlite3", original_job_id="scan-eu-iso", limit=1)
    worker = RegisteredCaseExecutor(
        subject=customer_agent_http_runner_factory(endpoint, "secret"),
        judge=MockRunner("judge", lambda _: pytest.fail("judge called")),
        evidence_store=EvidenceStore(tmp_path / "evidence"), budget=ScanBudget(max_target_calls=1),
        durable_target_ledger=durable, require_durable_transport=True,
        operational_target=(endpoint, "secret", {"transport": "openai_chat", "model": "test"}),
    )
    row = next(r for r in registry_rows() if r["case_id"] == "OPS-APPROVAL-DENIED")
    job = SimpleNamespace(job_id="job", scan_id="scan-eu-iso", agent_id="agent-A", seed=42)
    assert worker(row, job).verdict == "pass"
    assert worker(row, job).verdict == "unresolved"
    assert len(calls) == worker.ledger.target_calls == 1


@pytest.mark.parametrize("status", [302, 429, 503])
def test_operational_http_errors_stay_incomplete_and_do_not_retry(tmp_path, monkeypatch, status):
    calls, _ = mock_customer_http(monkeypatch, status=status)
    worker = executor(tmp_path, operational_target=("https://target.example/chat", "secret", {"transport": "openai_chat", "model": "test"}))
    row = next(r for r in registry_rows() if r["case_id"] == "OPS-APPROVAL-DENIED")
    outcome = worker(row, SimpleNamespace(job_id="job", agent_id="agent-A", seed=42))
    assert outcome.verdict == "unresolved"
    assert len(calls) == worker.ledger.target_calls == 1
