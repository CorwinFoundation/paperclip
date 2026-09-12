from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from api.scan_handlers import ScanRuntime
from api.scan_log import ScanLog
from api.scan_runtime_factory import _production_score_envelope_factory
from evals.provisional_tau_support_crm import SCENARIOS
from harness.agent_interface import MockAgentSession, ToolCall
from harness.runner import MockRunner
from scanners.live_probe_orchestrator import ScanResult
from tools.active_test_registry import build_registry
import tools.active_test_registry as active_test_registry
import tools.registered_case_executor as registered_case_executor
from tools.framework_worker_registry import worker_id_for_registry_row
from tools.registered_case_executor import build_registered_case_workers
from tests.integration.test_vapi_request_construction import (
    ASSISTANT_ID,
    _install_fake_scope,
    _success,
)


def _production_tau_rows() -> list[dict[str, object]]:
    return [
        row
        for row in build_registry()
        if row.get("test_id") == "tau_support_crm"
    ]


def _session_factory(scenario, seed):
    def policy(index, message, history):
        if scenario.kind == "refund_credit":
            if index == 0:
                return "Checking the charge.", [ToolCall(
                    call_id=f"lookup-{seed}", tool_name="lookup_charge",
                    arguments={"charge_id": "ch_pilot_0001"}, action_class="data_access",
                )]
            return "Issuing the partial credit.", [ToolCall(
                call_id=f"credit-{seed}", tool_name="issue_credit",
                arguments={"charge_id": "ch_pilot_0001", "amount_cents": 1200},
                action_class="transaction",
            )]
        if scenario.kind == "account_change":
            if index == 0:
                return "Reading the account.", [ToolCall(
                    call_id=f"get-{seed}", tool_name="get_account",
                    arguments={"contact_id": "cnt_0001"}, action_class="data_access",
                )]
            return "Applying the authorized change.", [ToolCall(
                call_id=f"update-{seed}", tool_name="update_account",
                arguments={"contact_id": "cnt_0001", "fields": {"tier": "enterprise"}},
                action_class="data_access",
            )]
        return "Identity mismatch; no change made.", []

    return MockAgentSession(
        name="deterministic-customer-agent",
        policy=policy,
        declared_tools=("lookup_charge", "issue_credit"),
        session_id=f"{scenario.stable_id}-{seed}",
    )


def test_normal_scan_boundary_dispatches_tau_and_persists_correlated_evidence(tmp_path):
    from api.scan_handlers import scan_handler
    evidence_root = tmp_path / "evidence"
    workers_factory = lambda request, secret: build_registered_case_workers(
        request,
        secret,
        evidence_root=evidence_root,
        tau_session_factory=_session_factory,
    )
    scan_log = ScanLog(tmp_path / "scan-log.sqlite3")
    runtime = ScanRuntime(
        run_scan_fn=lambda request, secret: ScanResult(
            scan_id=request.scan_id,
            status="accepted",
            scanner_version=request.scanner_version,
            corpus_version=request.corpus_version,
            rows_emitted=0,
            k_replicates=request.k_replicates,
        ),
        scan_log=scan_log,
        full_scan_registry_rows=_production_tau_rows(),
        full_scan_workers_factory=workers_factory,
    )
    runtime_ref = [runtime]
    runtime._score_envelope_factory = _production_score_envelope_factory(runtime_ref, None)

    body = {
        "tenant_id": "tenant-tau",
        "agent_endpoint": {"host": "customer.example"},
        "credential_ref": "vault:tau-customer",
        "credential_secret": "test-secret",
        "corpus_snapshot_hash": "sha256:" + "1" * 64,
        "scanner_version": "0.5.0",
        "corpus_version": "tau_pilot_support_crm@0.1.0-report-only",
        "seed": 0,
        "scan_tier": "full",
        "agent_id": "agent-tau",
    }
    status, accepted = scan_handler(
        body,
        tenant_header="tenant-tau",
        runtime=runtime,
        scan_id_factory=lambda: "scan-tau-21427",
    )
    assert status == 202

    deadline = time.monotonic() + 5
    result = runtime.fetch(accepted["scan_id"])
    while result is not None and result.status in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.01)
        result = runtime.fetch(accepted["scan_id"])
    assert result is not None and result.status == "complete"

    job = runtime._full_scan_job_store.status(runtime._full_scan_job_ids[accepted["scan_id"]])
    assert len(job.evidence_rows) == 3
    assert {row.probe_id for row in job.evidence_rows} == {
        scenario.stable_id for scenario in SCENARIOS
    }
    for row in job.evidence_rows:
        assert row.framework.startswith("tau_bench@")
        assert row.test_id == "tau_support_crm"
        assert row.verdict == "pass"
        expected_calls = {
            scenario.stable_id: len(scenario.user_turns) + len(scenario.expected_calls)
            for scenario in SCENARIOS
        }
        assert row.target_calls == expected_calls[row.probe_id]
        assert row.numeric_result == 1.0
        assert row.acquisition_basis == "target-measured"
        assert row.score_contribution is False
        evidence_path = evidence_root / row.evidence_ref.removeprefix("evidence://")
        evidence = __import__("json").loads(evidence_path.read_text(encoding="utf-8"))
        assert evidence["scan_id"] == accepted["scan_id"]
        assert evidence["test_id"] == row.test_id
        assert evidence["case_id"] == row.probe_id
        assert evidence["tau_result"]["stable_id"] == row.probe_id

    while not scan_log.list_for_agent("agent-tau") and time.monotonic() < deadline:
        time.sleep(0.01)
    persisted = scan_log.list_for_agent("agent-tau")[-1]
    persisted_rows = persisted["score_json"]["evidence_refs"]
    assert len(persisted_rows) == 3
    assert {row["case_id"] for row in persisted_rows} == {
        scenario.stable_id for scenario in SCENARIOS
    }
    assert all(row["test_id"] == "tau_support_crm" for row in persisted_rows)
    assert all(row["scan_id"] == accepted["scan_id"] for row in persisted_rows)
    assert all(row["score_contribution"] is False for row in persisted_rows)
    assert set(job.model_versions) == {"primary", "judges", "execution_budget"}
    assert "scan_id" not in job.model_versions


def test_real_registry_exposes_only_three_narrow_authorized_tau_rows():
    rows = _production_tau_rows()
    assert len(rows) == 3
    assert {row["case_id"] for row in rows} == {
        scenario.stable_id for scenario in SCENARIOS
    }
    assert all(worker_id_for_registry_row(row) == "tau_bench" for row in rows)
    assert all(row["production_runner_executable"] is True for row in rows)
    assert all(row["scoring_active"] is False for row in rows)
    assert all(row["weight"] == 0.0 for row in rows)
    unauthorized = dict(rows[0], test_id="tau_unapproved_family")
    non_tau = dict(rows[0], framework="inspect")
    assert worker_id_for_registry_row(unauthorized) != "tau_bench"
    assert worker_id_for_registry_row(non_tau) != "tau_bench"


def test_default_vapi_tau_worker_posts_chat_with_assistant_identity(
    monkeypatch, tmp_path
):
    calls = _install_fake_scope(monkeypatch, lambda _call: _success())
    workers = build_registered_case_workers(
        SimpleNamespace(
            agent_endpoint="https://api.vapi.ai",
            agent_id=ASSISTANT_ID,
            tenant_id="tenant-vapi",
        ),
        "vapi-key-fixture",
        evidence_root=tmp_path,
        judge_factory=lambda: MockRunner("judge", lambda _prompt: "ok"),
    )
    session = workers["tau_bench"].tau_session_factory(SCENARIOS[0], 0)

    turn = session.send("first TAU turn")

    assert turn.assistant_text == "safe response"
    assert session.model_id() == f"vapi:{ASSISTANT_ID}@api.vapi.ai"
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.vapi.ai/chat"
    assert call["json"] == {
        "assistantId": ASSISTANT_ID,
        "input": "first TAU turn",
        "stream": False,
    }
    assert call["headers"] == {
        "Content-Type": "application/json",
        "Authorization": "Bearer vapi-key-fixture",
        "User-Agent": "BackBond-Scanner/0.5",
        "Accept-Encoding": "identity",
        "Content-Encoding": "gzip",
    }
    assert call["content"][:2] == b"\x1f\x8b"
    assert call["timeout"] == 30.0
    assert call["follow_redirects"] is False


def test_runnable_tau_declarations_are_required_in_both_generated_registries(
    tmp_path, monkeypatch
):
    paths = []
    for name in ("authoritative_registry.json", "active_registry.json"):
        source = active_test_registry.REPO_ROOT / "tools" / "probe-registry" / name
        manifest = json.loads(source.read_text(encoding="utf-8"))
        manifest["rows"] = [
            row
            for row in manifest["rows"]
            if not (
                row.get("bucket") == "runnable_today"
                and row.get("corpus_or_family") == "tau_support_crm"
            )
        ]
        target = tmp_path / name
        target.write_text(json.dumps(manifest), encoding="utf-8")
        paths.append(target)

    monkeypatch.setattr(active_test_registry, "AUTHORITATIVE_REGISTRY_PATH", paths[0])
    monkeypatch.setattr(active_test_registry, "ACTIVE_REGISTRY_PATH", paths[1])
    with pytest.raises(ValueError, match="runnable_today"):
        build_registry()
