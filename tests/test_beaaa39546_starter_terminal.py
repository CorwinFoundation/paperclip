from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from api.accepted_scan_store import AcceptedScanStore
from api.scan_handlers import ScanRuntime, scan_status_handler
from harness.customer_runner import customer_agent_http_runner_factory
from harness.kaltura_genie_browser import KalturaGenieBrowserRunner
from scanners.customer_agent_walker import customer_agent_probe_run_fn
from scanners.live_probe_orchestrator import OrchestratorError, ScanRequest, ScanResult
from scanners.starter_forms import reserve_starter_cases
from scanners.variable_test_forms import FormStore


def request(scan_id="starter"):
    return ScanRequest(
        scan_id=scan_id,
        tenant_id="tenant",
        agent_endpoint="https://api.vapi.ai",
        credential_ref="secret-ref",
        corpus_snapshot_hash="abc",
        scanner_version="test",
        corpus_version="test",
        seed=1,
        pack_id="starter-100@4",
        include_result_rows=True,
        endpoint_config={"auth": {"type": "bearer"}},
    )


def test_complete_starter_fails_loudly_when_result_rows_are_missing():
    runtime = ScanRuntime(run_scan_fn=lambda *_: None)
    result = ScanResult(
        scan_id="starter",
        status="complete",
        scanner_version="test",
        corpus_version="test",
        rows_emitted=100,
        starter_result_rows=(),
    )
    with pytest.raises(OrchestratorError, match="starter_result_rows_count_mismatch"):
        runtime._record_completed_result(request(), result, agent_id=None)


def test_missing_rows_become_terminal_failure_and_callback_in_background(tmp_path, caplog):
    store = AcceptedScanStore(tmp_path / "accepted.sqlite3")
    outbox = MagicMock()
    runtime = ScanRuntime(
        run_scan_fn=lambda *_: ScanResult(
            scan_id="starter",
            status="complete",
            scanner_version="test",
            corpus_version="test",
            rows_emitted=100,
            starter_result_rows=(),
        ),
        accepted_scan_store=store,
        webhook_outbox=outbox,
    )
    req = request()
    store.accept(
        req,
        agent_id=None,
        invocation_context=None,
        webhook_cfg={"url": "https://intake.example/callback", "secret_ref": "wh"},
    )
    runtime._webhook_cfgs[req.scan_id] = {
        "url": "https://intake.example/callback",
        "secret_ref": "wh",
    }

    runtime._run_accepted_scan_background(
        scan_request=req, secret="secret", agent_id=None
    )

    diagnostic = caplog.text
    assert "scan_id=starter stage=result_finalization error_type=OrchestratorError" in diagnostic
    assert "starter_result_rows_count_mismatch:expected=100:actual=0" in diagnostic
    assert "_record_completed_result" in diagnostic
    row = store.get(req.scan_id)
    assert row["state"] == "terminal"
    assert row["result"].status == "failed"
    assert row["result"].incomplete_reason == (
        "scan_worker_error:stage=result_finalization:cause=starter_result_rows_count_mismatch"
    )
    outbox.enqueue.assert_called_once()
    assert outbox.enqueue.call_args.kwargs["payload"]["status"] == "failed"


def test_activity_count_is_monotonic_and_exposed():
    store = AcceptedScanStore(":memory:", clock=lambda: 12.0)
    req = request()
    store.accept(req, agent_id=None, invocation_context=None, webhook_cfg=None)
    store.mark_running(req.scan_id)
    store.note_activity(req.scan_id, completed_test_count=4)
    store.note_activity(req.scan_id, completed_test_count=2)
    runtime = ScanRuntime(run_scan_fn=lambda *_: None, accepted_scan_store=store)
    status, payload = scan_status_handler(req.scan_id, runtime=runtime)
    assert status == 200
    assert payload["last_activity_at"] == 12.0
    assert payload["completed_test_count"] == 4


def test_kaltura_starter_rows_survive_persistence_reload_and_callback(
    tmp_path, monkeypatch
):
    def fake_complete(self, prompt):
        reply = f"observed reply for {prompt}"
        self._pending_exchange = (prompt, reply)
        return reply

    monkeypatch.setattr(KalturaGenieBrowserRunner, "complete", fake_complete)
    runner = customer_agent_http_runner_factory(
        "https://learning.kaltura.com/",
        "",
        endpoint_config={
            "transport": "kaltura_genie_browser",
            "auth": {"type": "none"},
            "_include_result_rows": True,
        },
    )
    runner.pack_id = "starter-100@4"
    runner.starter_cases, runner.starter_form_summary = reserve_starter_cases(
        scan_id="kaltura-starter",
        tenant_id="tenant",
        agent_id="kaltura",
        store=FormStore(tmp_path / "forms.sqlite3"),
    )
    emitted = list(customer_agent_probe_run_fn(runner))
    rows = runner.starter_result_rows()
    assert len(emitted) == len(rows) == 100

    req = request("kaltura-starter")
    req = replace(
        req,
        agent_endpoint="https://learning.kaltura.com/",
        endpoint_config={
            "transport": "kaltura_genie_browser",
            "auth": {"type": "none"},
            "_include_result_rows": True,
        },
    )
    result = ScanResult(
        scan_id=req.scan_id,
        status="complete",
        scanner_version=req.scanner_version,
        corpus_version=req.corpus_version,
        rows_emitted=100,
        starter_result_rows=rows,
    )
    db = tmp_path / "accepted.sqlite3"
    store = AcceptedScanStore(db)
    outbox = MagicMock()
    runtime = ScanRuntime(
        run_scan_fn=lambda *_: result,
        accepted_scan_store=store,
        webhook_outbox=outbox,
    )
    webhook = {"url": "https://intake.example/callback", "secret_ref": "wh"}
    store.accept(req, agent_id=None, invocation_context=None, webhook_cfg=webhook)
    runtime._webhook_cfgs[req.scan_id] = webhook
    runtime._record_completed_result(req, result, agent_id=None)
    runtime._maybe_deliver_terminal_webhook(req.scan_id, result)
    store.close()

    reloaded = AcceptedScanStore(db).get(req.scan_id)["result"]
    assert len(reloaded.starter_result_rows) == 100
    assert reloaded.starter_result_rows[0]["prompt"] == rows[0]["prompt"]
    payload = outbox.enqueue.call_args.kwargs["payload"]
    assert len(payload["result_rows"]) == 100
