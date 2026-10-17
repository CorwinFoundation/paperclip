"""Durable P08 target-send accounting on the real customer HTTP boundary."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.customer_runner import customer_agent_http_runner_factory
from harness.runner import MockRunner
from tools.framework_workers import build_live_framework_workers
from tools.active_test_registry import build_registry
from tools.registered_case_executor import (
    BudgetExceeded,
    DurableTargetAttemptLedger,
    EvidenceStore,
    RegisteredCaseExecutor,
    ScanBudget,
)


@pytest.fixture
def tenant_receiver():
    received: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(payload["prompt"])
            body = json.dumps({"response": "Policy refusal."}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/probe", received
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def _executor(tmp_path, endpoint, *, scan_id="accepted-scan-p08"):
    budget = ScanBudget(max_target_calls=40, max_judge_calls=0)
    return RegisteredCaseExecutor(
        subject=customer_agent_http_runner_factory(endpoint, "secret"),
        judge=MockRunner("must-not-run", lambda _: pytest.fail("judge called")),
        evidence_store=EvidenceStore(tmp_path / "evidence"),
        budget=budget,
        tenant_id="tenant-p08",
        durable_target_ledger=DurableTargetAttemptLedger(
            tmp_path / "attempts.sqlite3", original_job_id=scan_id, limit=40
        ),
        require_durable_transport=True,
    )


def test_eight_cases_make_exactly_40_durable_http_sends_and_zero_judges(
    tmp_path, tenant_receiver
):
    endpoint, received = tenant_receiver
    rows = [r for r in build_registry() if r["test_id"] == "cross_customer_data_exposure"
            and r["case_id"].startswith("TENANT-")]
    assert len(rows) == 8
    executor = _executor(tmp_path, endpoint)
    job = SimpleNamespace(job_id="job-p08", scan_id="accepted-scan-p08",
                          agent_id="agent", seed=41663,
                          capability_manifest={"multi_tenant_data_access": True})
    results = [executor(row, job) for row in rows]
    assert all(r.status == "complete" and r.target_calls == 5 for r in results)
    assert len(received) == 40
    assert executor.durable_target_ledger.snapshot() == {
        "reserved": 40, "complete": 40, "unknown": 0, "limit": 40,
    }
    assert executor.ledger.snapshot()["judge_calls"] == 0


def test_unknown_reservation_survives_restart_and_is_not_rebought(tmp_path):
    path = tmp_path / "attempts.sqlite3"
    first = DurableTargetAttemptLedger(path, original_job_id="scan", limit=40)
    first.reserve("TENANT-01:0")
    restarted = DurableTargetAttemptLedger(path, original_job_id="scan", limit=40)
    with pytest.raises(BudgetExceeded, match="reserved_not_repurchaseable"):
        restarted.reserve("TENANT-01:0")
    assert restarted.snapshot()["unknown"] == 1


def test_durable_ceiling_never_exceeds_40(tmp_path):
    ledger = DurableTargetAttemptLedger(
        tmp_path / "attempts.sqlite3", original_job_id="scan", limit=40
    )
    for index in range(40):
        ledger.reserve(f"case:{index}")
    with pytest.raises(BudgetExceeded, match="max_target_calls"):
        ledger.reserve("case:40")
    assert ledger.snapshot()["reserved"] == 40


def test_targeted_factory_requires_explicit_stable_ledger_path(monkeypatch, tmp_path):
    request = SimpleNamespace(
        agent_endpoint="https://agent.example.test/probe", agent_id="agent",
        tenant_id="tenant", scan_id="original-scan",
        operations_scope={"requested_case_ids": ["TENANT-01"]},
    )
    monkeypatch.delenv("ARE_FULL_SCAN_ATTEMPT_LEDGER_PATH", raising=False)
    with pytest.raises(RuntimeError, match="requires ARE_FULL_SCAN_ATTEMPT_LEDGER_PATH"):
        build_live_framework_workers(
            request, "secret", evidence_root=tmp_path / "evidence-a",
            subject_factory=customer_agent_http_runner_factory,
            judge_factory=lambda: MockRunner("judge", lambda _: "unused"),
        )


def test_targeted_factory_reuses_ledger_when_evidence_root_changes(monkeypatch, tmp_path):
    ledger_path = tmp_path / "maintained" / "attempts.sqlite3"
    monkeypatch.setenv("ARE_FULL_SCAN_ATTEMPT_LEDGER_PATH", str(ledger_path))
    request = SimpleNamespace(
        agent_endpoint="https://agent.example.test/probe", agent_id="agent",
        tenant_id="tenant", scan_id="original-scan",
        operations_scope={"requested_case_ids": ["TENANT-01"]},
    )
    kwargs = {
        "subject_factory": customer_agent_http_runner_factory,
        "judge_factory": lambda: MockRunner("judge", lambda _: "unused"),
    }
    first = build_live_framework_workers(
        request, "secret", evidence_root=tmp_path / "evidence-a", **kwargs
    )["promptfoo"]
    first.durable_target_ledger.reserve("TENANT-01:0")

    restarted = build_live_framework_workers(
        request, "secret", evidence_root=tmp_path / "evidence-b", **kwargs
    )["promptfoo"]
    with pytest.raises(BudgetExceeded, match="reserved_not_repurchaseable"):
        restarted.durable_target_ledger.reserve("TENANT-01:0")
    assert restarted.durable_target_ledger.snapshot()["unknown"] == 1


def test_live_service_owns_absolute_persistent_ledger_and_factory_uses_it(
    monkeypatch, tmp_path
):
    unit = (
        Path(__file__).parents[2]
        / "infra/live-edge/backbond-scanner-live.service"
    ).read_text()
    assignment = next(
        line.removeprefix("Environment=ARE_FULL_SCAN_ATTEMPT_LEDGER_PATH=")
        for line in unit.splitlines()
        if line.startswith("Environment=ARE_FULL_SCAN_ATTEMPT_LEDGER_PATH=")
    )
    assert assignment == "/var/lib/are-scanner/full-scan-target-attempts.sqlite3"
    assert Path(assignment).is_absolute()
    assert (
        "--env ARE_FULL_SCAN_ATTEMPT_LEDGER_PATH=${ARE_FULL_SCAN_ATTEMPT_LEDGER_PATH}"
        in unit
    )

    # Use an equivalent writable absolute path to prove the runtime factory,
    # not test-only constructor injection, consumes the deployed variable.
    runtime_path = tmp_path / "var/lib/are-scanner/full-scan-target-attempts.sqlite3"
    monkeypatch.setenv("ARE_FULL_SCAN_ATTEMPT_LEDGER_PATH", str(runtime_path))
    request = SimpleNamespace(
        agent_endpoint="https://agent.example.test/probe", agent_id="agent",
        tenant_id="tenant", scan_id="original-service-scan",
        operations_scope={"requested_case_ids": ["TENANT-01"]},
    )
    worker = build_live_framework_workers(
        request, "secret", evidence_root=tmp_path / "evidence",
        subject_factory=customer_agent_http_runner_factory,
        judge_factory=lambda: MockRunner("judge", lambda _: "unused"),
    )["promptfoo"]
    worker.durable_target_ledger.reserve("TENANT-01:0")
    assert runtime_path.exists()
