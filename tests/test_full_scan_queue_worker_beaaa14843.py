"""BEAAA-14843 DB-backed full-scan queue/worker lifecycle tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from api.full_scan_executor import (
    FullScanJobStore,
    WorkerAttemptResult,
    registry_cases_from_rows,
)
from api.full_scan_job_persistence import FullScanJobPersistence
from api.full_scan_worker import (
    FullScanQueueWorker,
    load_default_full_scan_registry_rows,
)
from tools.active_test_registry import (
    count_active_executable,
    distinct_case_counts,
)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 16, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _registry_rows() -> list[dict]:
    return [
        {
            "test_id": "promptinject",
            "case_id": "promptinject.case_001",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.1",
            "runner": "promptfoo_wrapper",
        },
        {
            "test_id": "promptinject",
            "case_id": "promptinject.case_002",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.1",
            "runner": "promptfoo_wrapper",
        },
    ]


def _worker(row, job):
    return WorkerAttemptResult(
        status="complete",
        evidence_ref=f"evidence://{job.job_id}/{row['case_id']}.json",
        model_version="fixture-agent-v0",
    )


def test_default_registry_loader_uses_live_production_registry(monkeypatch):
    live_rows = [
        {
            "test_id": "candidate",
            "case_id": "candidate.case_001",
            "status": "active",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.1",
            "runner": "content_candidate_worker",
            "target_bound": True,
            "runtime_case_identity": "candidate.case_001",
        },
        {
            "test_id": "candidate",
            "case_id": "candidate.inactive",
            "status": "inactive",
            "production_runner_executable": True,
            "risk_or_rubric_id": "P3.1",
            "runner": "content_candidate_worker",
            "target_bound": True,
            "runtime_case_identity": "candidate.inactive",
        },
    ]
    monkeypatch.setattr(
        "tools.active_test_registry.build_registry",
        lambda: live_rows,
    )

    assert load_default_full_scan_registry_rows() == (live_rows[0],)


def test_default_registry_loader_matches_standard_production_count():
    rows = list(load_default_full_scan_registry_rows())

    # BEAAA-29085 gates unbound production adapters, retiring the BEAAA-24273
    # content-candidate inventory invariant for those five families.
    # BEAAA-29742 gates eight more the same way, so the executable, distinct
    # and content-candidate counts all step down together. BEAAA-30128 then
    # binds 60 counterfactual-stability rows back into the production census.
    assert count_active_executable(rows) == 2_373
    # Conditional operational receipts execute without increasing the published
    # measured-test claim; request selection gates the 26 conditional cases.
    from tools.assessment_test_catalog import FAMILIES
    assert len(rows) == 2_373 + 26
    assert sum(row.get("test_id") in FAMILIES for row in rows) == 26
    assert distinct_case_counts(rows)["total"] == 2_372
    assert (
        sum(row.get("content_candidate_inventory") is True for row in rows)
        == 2_000
    )


def test_db_backed_worker_restart_resumes_without_duplicate_attempts(monkeypatch, tmp_path):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-14843-test-key")
    db = str(tmp_path / "full_scan_queue.sqlite3")
    rows = _registry_rows()
    clock1 = Clock()

    store1 = FullScanJobStore(clock=clock1, persistence=FullScanJobPersistence(db))
    job = store1.submit(
        agent_id="agent-14843",
        scanner_version="1.3.0",
        corpus_version="registry-baseline-2026-07-13",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=registry_cases_from_rows(rows),
        idempotency_key="tenant-14843:scan-1:full",
        wall_clock_budget_seconds=14_400,
        rate_limit_per_minute=120,
    )

    first = FullScanQueueWorker(
        store1,
        registry_rows=rows,
        workers={"promptfoo": _worker},
        lease_ttl_seconds=300,
    ).run_once(max_cases=1)
    assert first.status == "interrupted"
    assert store1.status(job.job_id).status == "running"
    assert len(store1.status(job.job_id).evidence_rows) == 1
    del store1

    # New process: the durable store recovers the accepted running job.
    clock2 = Clock()
    store2 = FullScanJobStore(clock=clock2, persistence=FullScanJobPersistence(db))
    worker2 = FullScanQueueWorker(
        store2,
        registry_rows=rows,
        workers={"promptfoo": _worker},
        lease_ttl_seconds=300,
    )
    recovery = worker2.recover()
    assert recovery["resumed"] == [job.job_id]

    second = worker2.run_once()
    assert second.status == "finalized"
    assert second.processed_cases == 1
    finalized = store2.status(job.job_id)
    assert finalized.status == "complete"
    assert finalized.final_manifest is not None
    assert finalized.final_manifest["counts"] == {
        "expected": 2,
        "evidence_rows": 2,
        "missing_required": 0,
    }
    assert finalized.final_manifest["execution_budget"] == {
        "wall_clock_budget_seconds": 14_400,
        "rate_limit_per_minute": 120,
    }
    assert len(finalized.evidence_rows) == 2
    assert {row.probe_id for row in finalized.evidence_rows} == {
        "promptinject.case_001",
        "promptinject.case_002",
    }
    assert len(finalized.attempts) == 2


def test_targeted_partial_batch_restart_never_widens_dispatch(monkeypatch, tmp_path):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-41663-targeted-test-key")
    db = str(tmp_path / "targeted-queue.sqlite3")
    rows = _registry_rows() + [{
        "test_id": "promptinject", "case_id": "promptinject.unselected",
        "status": "active", "production_runner_executable": True,
        "risk_or_rubric_id": "P3.1", "runner": "promptfoo_wrapper",
    }]
    requested = ["promptinject.case_001", "promptinject.case_002"]
    dispatched: list[tuple[str, str | None]] = []

    def scoped_worker(row, job):
        dispatched.append((row["case_id"], job.scan_id))
        return _worker(row, job)

    clock = Clock()
    store1 = FullScanJobStore(clock=clock, persistence=FullScanJobPersistence(db))
    job = store1.submit(
        agent_id="agent-41663", scanner_version="1.3.0",
        corpus_version="registry@1", model_versions={"primary": "fixture"}, seed=41663,
        registry_cases=registry_cases_from_rows(rows[:2]),
        scan_id="original-scan-41663",
        operations_scope={"requested_case_ids": requested},
    )
    worker1 = FullScanQueueWorker(
        store1, registry_rows=rows, workers={"promptfoo": scoped_worker},
        lease_ttl_seconds=300, case_concurrency=1,
    )
    assert [row["case_id"] for row in worker1._pending_registry_rows_v2(job.job_id)] == requested
    first = worker1.run_once(max_cases=1)
    assert first.status == "interrupted"
    assert len(store1.status(job.job_id).attempts) == 1

    clock.advance(301)
    store2 = FullScanJobStore(clock=clock, persistence=FullScanJobPersistence(db))
    worker2 = FullScanQueueWorker(
        store2, registry_rows=rows, workers={"promptfoo": scoped_worker},
        lease_ttl_seconds=300, case_concurrency=1,
    )
    assert worker2.recover()["resumed"] == [job.job_id]
    assert worker2.run_once().status == "finalized"

    resumed = store2.status(job.job_id)
    assert dispatched == [
        ("promptinject.case_001", "original-scan-41663"),
        ("promptinject.case_002", "original-scan-41663"),
    ]
    assert len(resumed.attempts) == 2
    assert resumed.scan_id == "original-scan-41663"
    assert resumed.operations_scope["requested_case_ids"] == requested


def test_targeted_worker_fails_closed_when_persisted_case_is_absent():
    rows = _registry_rows()
    store = FullScanJobStore()
    job = store.submit(
        agent_id="agent-41663", scanner_version="1.3.0",
        corpus_version="registry@1", model_versions={}, seed=41663,
        registry_cases=registry_cases_from_rows(rows),
        operations_scope={"requested_case_ids": ["missing-case"]},
    )
    worker = FullScanQueueWorker(store, registry_rows=rows, workers={"promptfoo": _worker})
    with pytest.raises(RuntimeError, match="cases unavailable"):
        worker._pending_registry_rows(job.job_id)
    with pytest.raises(RuntimeError, match="cases unavailable"):
        worker._pending_registry_rows_v2(job.job_id)


def test_poisoned_targeted_job_is_terminal_once_and_does_not_starve_queue(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-41663-poison-test-key")
    db = tmp_path / "poison-queue.sqlite3"
    rows = _registry_rows()
    callback_payloads: list[dict] = []
    callback_headers: list[dict[str, str]] = []

    def post(_url, headers, body):
        import json
        callback_headers.append(dict(headers))
        callback_payloads.append(json.loads(body))

    store = FullScanJobStore(persistence=FullScanJobPersistence(db))
    poison = store.submit(
        agent_id="poison-agent", scanner_version="1.3.0",
        corpus_version="registry@1", model_versions={}, seed=1,
        registry_cases=registry_cases_from_rows(rows[:1]),
        scan_id="original-poison-scan",
        operations_scope={"requested_case_ids": ["retired.case"]},
        terminal_webhook={
            "webhook_cfg": {"url": "https://callback.invalid", "secret_ref": "x"},
            "response_base": {"scan_id": "original-poison-scan"},
        },
    )
    healthy = store.submit(
        agent_id="healthy-agent", scanner_version="1.3.0",
        corpus_version="registry@1", model_versions={}, seed=2,
        registry_cases=registry_cases_from_rows(rows), scan_id="healthy-scan",
    )
    sends: list[str] = []

    def counted_worker(row, job):
        sends.append(job.job_id)
        return _worker(row, job)

    worker = FullScanQueueWorker(
        store, registry_rows=rows, workers={"promptfoo": counted_worker},
        webhook_secret_resolver=lambda _ref: "secret",
        webhook_http_poster=post,
    )
    result = worker.run_once()
    assert result.status == "invalid_job_finalized"
    terminal = store.status(poison.job_id)
    assert terminal.status == "failed"
    assert terminal.scan_id == "original-poison-scan"
    assert terminal.final_manifest["terminal_failure_reason"].startswith(
        "invalid_persisted_operations_scope:"
    )
    assert terminal.final_manifest["counts"]["missing_required"] == 1
    assert sends == []

    # The released terminal callback drains once, then the unrelated full job runs.
    assert worker.run_once().status == "terminal_webhook_delivered"
    assert callback_payloads[0]["missing_evidence"] is True
    assert callback_payloads[0]["comparison_status"] == "no_comparable_result"
    assert set(callback_headers[0]) == {
        "X-BackBond-Signature",
        "X-BackBond-Delivery",
        "Content-Type",
    }
    assert callback_headers[0]["Content-Type"] == "application/json"
    assert worker.run_once().status == "finalized"
    assert store.status(healthy.job_id).status == "complete"
    assert sends == [healthy.job_id, healthy.job_id]

    # Repeated recovery/run cycles neither requeue poison nor replay its callback.
    restarted = FullScanQueueWorker(
        FullScanJobStore(persistence=FullScanJobPersistence(db)),
        registry_rows=rows, workers={"promptfoo": counted_worker},
        webhook_secret_resolver=lambda _ref: "secret", webhook_http_poster=post,
    )
    assert restarted.recover() == {"resumed": [], "failed": []}
    assert restarted.run_once().status == "idle"
    assert len(callback_payloads) == 1


def test_production_terminal_poster_forwards_signed_headers_to_http_server(tmp_path):
    """Exercise the real urllib poster, not the injectable fake seam."""
    received: dict[str, object] = {}

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.update(
                path=self.path,
                headers={key.lower(): value for key, value in self.headers.items()},
                body=body,
            )
            self.send_response(204)
            self.end_headers()

        def log_message(self, _format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        rows = _registry_rows()
        store = FullScanJobStore(
            persistence=FullScanJobPersistence(tmp_path / "wire-callback.sqlite3")
        )
        poison = store.submit(
            agent_id="wire-agent",
            scanner_version="1.3.0",
            corpus_version="registry@1",
            model_versions={},
            seed=42228,
            registry_cases=registry_cases_from_rows(rows[:1]),
            scan_id="wire-scan-42228",
            operations_scope={"requested_case_ids": ["retired.case"]},
            terminal_webhook={
                "webhook_cfg": {
                    "url": f"http://127.0.0.1:{server.server_port}/operations/callback",
                    "secret_ref": "wire-secret-ref",
                },
                "response_base": {"scan_id": "wire-scan-42228"},
            },
        )
        worker = FullScanQueueWorker(
            store,
            registry_rows=rows,
            workers={"promptfoo": _worker},
            webhook_secret_resolver=lambda _ref: "wire-shared-secret",
        )

        assert worker.run_once().status == "invalid_job_finalized"
        assert worker.run_once().status == "terminal_webhook_delivered"
        assert store.status(poison.job_id).terminal_webhook_delivered_at is not None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    headers = received["headers"]
    assert received["path"] == "/operations/callback"
    assert headers["content-type"] == "application/json"
    assert headers["x-backbond-signature"].startswith("sha256=")
    assert headers["x-backbond-delivery"]
    payload = json.loads(received["body"])
    assert payload["scan_id"] == "wire-scan-42228"
    assert payload["terminal_status_version"] == "scanner_terminal_status_v1"
    assert payload["missing_evidence"] is True
    assert payload["comparison_status"] == "no_comparable_result"


def test_lease_next_returns_none_until_job_is_durably_accepted(tmp_path):
    store = FullScanJobStore(persistence=FullScanJobPersistence(tmp_path / "queue.sqlite3"))
    assert store.lease_next() is None


def test_submit_rejects_invalid_budget_or_rate_limit():
    store = FullScanJobStore()
    with pytest.raises(Exception, match="wall_clock_budget_seconds must be positive"):
        store.submit(
            agent_id="agent-14843",
            scanner_version="1.3.0",
            corpus_version="registry-baseline-2026-07-13",
            model_versions={"primary": "fixture-agent-v0", "judges": []},
            seed=0,
            registry_cases=registry_cases_from_rows(_registry_rows()),
            wall_clock_budget_seconds=0,
        )
