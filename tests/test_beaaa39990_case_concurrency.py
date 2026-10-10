import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import api.full_scan_executor as full_scan_executor
from api.full_scan_executor import (
    FullScanJobStore,
    FullSuiteCase,
    WorkerAttemptResult,
    dispatch_registry_attempt,
)
from api.full_scan_worker import FullScanQueueWorker
from harness.agent_interface import ToolCall
from harness.mock_tools import PilotMapping
from harness.mock_tools.sandbox import ReferenceToolSandbox


def test_customer_case_pool_runs_concurrently_and_preserves_evidence_order():
    active = 0
    peak = 0
    lock = threading.Lock()

    def worker(row, job):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        case_id = row["case_id"]
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=f"evidence://{job.job_id}/{case_id}.json",
            verdict="pass",
        )

    rows = [
        {"test_id": "promptinject", "case_id": case_id, "status": "active",
         "production_runner_executable": True, "risk_or_rubric_id": "P1.1",
         "runner": "promptfoo_wrapper"}
        for case_id in ("case-c", "case-a", "case-b")
    ]
    store = FullScanJobStore()
    job = store.submit(
        agent_id="agent",
        scanner_version="test",
        corpus_version="test",
        model_versions={"primary": "test"},
        seed=0,
        registry_cases=tuple(FullSuiteCase(item["case_id"], "P1.1") for item in rows),
        tenant_id="tenant",
        target_id="target",
    )
    result = FullScanQueueWorker(
        store, registry_rows=rows, workers={"promptfoo": worker}
    ).run_once()

    assert result.status == "finalized"
    assert peak >= 2
    assert [row.probe_id for row in store.status(job.job_id).evidence_rows] == [
        "case-a", "case-b", "case-c"
    ]


def _run_ordered_scan(max_workers):
    rows = [
        {"test_id": "family-a", "case_id": case_id, "status": "active",
         "production_runner_executable": True, "risk_or_rubric_id": "P1.1",
         "runner": "promptfoo_wrapper"}
        for case_id in ("family-a.case-c", "family-a.case-a", "family-b.case-b")
    ]
    store = FullScanJobStore()
    job = store.submit(
        agent_id="agent", scanner_version="test", corpus_version="test",
        model_versions={"primary": "test"}, seed=11,
        registry_cases=tuple(FullSuiteCase(r["case_id"], "P1.1") for r in rows),
        tenant_id="tenant", target_id="target",
    )
    lease = store.lease(job.job_id)

    def worker(row, _job):
        time.sleep(0.002)
        return WorkerAttemptResult(
            status="complete", evidence_ref=f"evidence://{_job.job_id}/{row['case_id']}.json",
            verdict="pass", target_calls=1,
        )

    dispatch_registry_attempt(
        store, job.job_id, attempt_id=lease["attempt_id"], registry_rows=rows,
        workers={"promptfoo": worker}, max_workers=max_workers,
    )
    return tuple((row.probe_id, row.status, row.verdict, row.seed) for row in store.status(job.job_id).evidence_rows)


def test_sequential_and_concurrent_case_results_have_identical_order_and_verdicts():
    assert _run_ordered_scan(1) == _run_ordered_scan(3)


def test_transport_429_is_retried_with_bounded_backoff_and_then_succeeds(monkeypatch):
    calls = 0
    sleeps = []

    def worker(row, job):
        nonlocal calls
        calls += 1
        if calls < 3:
            return WorkerAttemptResult(
                status="incomplete", evidence_ref=None,
                incomplete_reason="upstream_http_429",
            )
        return WorkerAttemptResult(
            status="complete", evidence_ref=f"evidence://{job.job_id}/{row['case_id']}.json",
            verdict="pass",
        )

    monkeypatch.setattr(full_scan_executor.time, "sleep", sleeps.append)
    monkeypatch.setattr(full_scan_executor.random, "random", lambda: 0.5)
    rows = [{"case_id": "retry.case", "status": "active", "production_runner_executable": True,
             "risk_or_rubric_id": "P1.1", "runner": "promptfoo_wrapper"}]
    store = FullScanJobStore()
    job = store.submit(agent_id="a", scanner_version="s", corpus_version="c", model_versions={"primary": "m"},
                       seed=0, registry_cases=(FullSuiteCase("retry.case", "P1.1"),), target_id="t")
    lease = store.lease(job.job_id)
    dispatch_registry_attempt(store, job.job_id, attempt_id=lease["attempt_id"], registry_rows=rows,
                              workers={"promptfoo": worker})
    assert calls == 3
    assert sleeps and all(0 <= value <= 8 for value in sleeps)
    assert store.status(job.job_id).evidence_rows[0].status == "complete"


def test_per_target_case_cap_is_lower_than_worker_pool():
    active = 0
    peak = 0
    lock = threading.Lock()

    def worker(row, job):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return WorkerAttemptResult(
            status="complete",
            evidence_ref=f"evidence://{job.job_id}/{row['case_id']}.json",
        )

    rows = [
        {"case_id": f"cap.case-{i}", "status": "active",
         "production_runner_executable": True, "risk_or_rubric_id": "P1.1",
         "runner": "promptfoo_wrapper"}
        for i in range(6)
    ]
    store = FullScanJobStore()
    job = store.submit(
        agent_id="a", scanner_version="s", corpus_version="c",
        model_versions={"primary": "m"}, seed=0,
        registry_cases=tuple(FullSuiteCase(row["case_id"], "P1.1") for row in rows),
        target_id="same-target", max_concurrent_per_target=2,
    )
    lease = store.lease(job.job_id)
    dispatch_registry_attempt(
        store, job.job_id, attempt_id=lease["attempt_id"], registry_rows=rows,
        workers={"promptfoo": worker}, max_workers=4,
    )
    assert peak == 2


def test_wall_clock_breach_stops_admitting_cases_and_marks_pending_rows():
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    rows = [
        {"case_id": f"clock.case-{i}", "status": "active",
         "production_runner_executable": True, "risk_or_rubric_id": "P1.1",
         "runner": "promptfoo_wrapper"}
        for i in range(4)
    ]
    store = FullScanJobStore(clock=lambda: now[0])
    job = store.submit(
        agent_id="a", scanner_version="s", corpus_version="c",
        model_versions={"primary": "m"}, seed=0,
        registry_cases=tuple(FullSuiteCase(row["case_id"], "P1.1") for row in rows),
        target_id="clock-target", wall_clock_budget_seconds=1,
    )
    lease = store.lease(job.job_id)
    calls = []

    def worker(row, _job):
        calls.append(row["case_id"])
        now[0] = now[0] + timedelta(seconds=2)
        return WorkerAttemptResult(status="complete", evidence_ref="evidence://ok.json")

    dispatch_registry_attempt(
        store, job.job_id, attempt_id=lease["attempt_id"], registry_rows=rows,
        workers={"promptfoo": worker}, max_workers=2,
    )
    final = store.status(job.job_id)
    assert len(calls) == 2
    assert {row.incomplete_reason for row in final.evidence_rows if row.status == "incomplete"} == {
        "wall_clock_cap_reached"
    }
    assert len(final.evidence_rows) == 4


def test_exhausted_transport_retries_are_explicit_could_not_run(monkeypatch):
    monkeypatch.setattr(full_scan_executor.time, "sleep", lambda _delay: None)
    rows = [{"case_id": "retry.exhausted", "status": "active", "production_runner_executable": True,
             "risk_or_rubric_id": "P1.1", "runner": "promptfoo_wrapper"}]
    def worker(_row, _job):
        return WorkerAttemptResult(status="incomplete", evidence_ref=None, incomplete_reason="upstream_http_503")
    store = FullScanJobStore()
    job = store.submit(agent_id="a", scanner_version="s", corpus_version="c", model_versions={"primary": "m"},
                       seed=0, registry_cases=(FullSuiteCase("retry.exhausted", "P1.1"),), target_id="t")
    lease = store.lease(job.job_id)
    dispatch_registry_attempt(store, job.job_id, attempt_id=lease["attempt_id"], registry_rows=rows,
                              workers={"promptfoo": worker})
    row = store.status(job.job_id).evidence_rows[0]
    assert row.status == "could_not_run"
    assert row.incomplete_reason == "transport_retry_exhausted:upstream_http_503:attempts=4"


def test_progress_exposes_inflight_and_family_rollups():
    store = FullScanJobStore()
    job = store.submit(agent_id="a", scanner_version="s", corpus_version="c", model_versions={"primary": "m"},
                       seed=0, registry_cases=(FullSuiteCase("safety.case", "P1.1"), FullSuiteCase("privacy.case", "P1.2")),
                       tenant_id="tenant", target_id="target")
    lease = store.lease(job.job_id)
    store.set_in_flight(job.job_id, attempt_id=lease["attempt_id"], case_ids=("privacy.case",))
    progress = store.progress(job.job_id, tenant_id="tenant")
    assert progress["rows_done"] == 0 and progress["rows_total"] == 2
    assert progress["in_flight"] == 1
    assert progress["families"]["privacy"]["in_flight"] == 1


def test_concurrent_conflicting_money_cases_have_private_sandbox_state():
    mapping = PilotMapping({
        "partner": "isolation-test",
        "authorized_scopes": ["ledger:transfer", "email:send", "fixture:retrieval"],
        "tool_bindings": [
            {"partner_tool": "transfer", "surface": "ledger", "operation": "transfer_funds"},
            {"partner_tool": "mail", "surface": "email", "operation": "send_email"},
            {"partner_tool": "kb_poison", "surface": "retrieval_source_poisoning", "operation": "poison_source"},
        ],
    })

    def run_case(case_id, amount):
        sandbox = ReferenceToolSandbox(mapping, session_id=case_id)
        sandbox.handle(ToolCall("transfer", "transfer", {"recipient_id": case_id, "amount_cents": amount}, "transaction"))
        sandbox.handle(ToolCall("mail", "mail", {"to": "qa@sandbox.backbond.test", "body": case_id}, "message_send"))
        sandbox.handle(ToolCall("kb", "kb_poison", {}, "tool_call"))
        return (
            sandbox.surface_state("ledger"),
            sandbox.surface_state("email"),
            sandbox.surface_state("retrieval_source_poisoning"),
            sandbox.ledger,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = tuple(pool.map(run_case, ("case-a", "case-b"), (100, 250)))
    assert first[0]["accounts"]["acct_source"]["balance_cents"] == 999900
    assert second[0]["accounts"]["acct_source"]["balance_cents"] == 999750
    assert first[0]["transfers"]["tr_0000"]["recipient_id"] == "case-a"
    assert second[0]["transfers"]["tr_0000"]["recipient_id"] == "case-b"
    assert first[1]["outbox"][0]["body"] == "case-a"
    assert second[1]["outbox"][0]["body"] == "case-b"
    assert first[2]["revision"] == second[2]["revision"] == 2
    assert len(first[3].rows()) == len(second[3].rows()) == 3
