from __future__ import annotations

import pytest

from api.scan_handlers import HandlerError, ScanRuntime, _customer_payload, scan_handler
from harness.customer_runner import customer_agent_http_runner_factory
from scanners.customer_agent_walker import customer_agent_probe_run_fn
from scanners.live_probe_orchestrator import ScanResult
from scanners.starter_pack import STARTER_PACK_ID


def test_starter_result_row_redacts_credential_and_system_prompt() -> None:
    runner = customer_agent_http_runner_factory(
        "https://agent.example.test/v1/chat",
        "credential-secret",
        endpoint_config={
            "transport": "openai_chat",
            "model": "test-model",
            "system_prompt": "private system prompt",
            "auth": {"type": "bearer"},
            "_include_result_rows": True,
        },
    )
    runner._pending_exchange = (
        "test credential-secret",
        "echo private system prompt and credential-secret",
    )
    runner._pending_exchange = tuple(runner._redact_result_text(v) for v in runner._pending_exchange)
    runner.record_starter_verdict(
        test_id="P1.1-FAC-001", family="p1_1_factuality_golden", verdict="passed"
    )
    rows = runner.starter_result_rows()
    assert rows == ({
        "test_id": "P1.1-FAC-001",
        "family": "p1_1_factuality_golden",
        "prompt": "test [REDACTED]",
        "reply": "echo [REDACTED] and [REDACTED]",
        "verdict": "passed",
    },)
    assert "credential-secret" not in repr(rows)
    assert "private system prompt" not in repr(rows)


def test_vapi_factory_preserves_result_row_opt_in() -> None:
    runner = customer_agent_http_runner_factory(
        "https://api.vapi.ai",
        "credential-secret",
        "11111111-1111-4111-8111-111111111111",
        endpoint_config={"auth": {"type": "bearer"}, "_include_result_rows": True},
    )
    runner._pending_exchange = ("sent prompt", "real target reply")
    runner.record_starter_verdict(
        test_id="starter-case", family="prompt_injection", verdict="passed"
    )
    assert runner.starter_result_rows() == ({
        "test_id": "starter-case",
        "family": "prompt_injection",
        "prompt": "sent prompt",
        "reply": "real target reply",
        "verdict": "passed",
    },)


def test_kaltura_factory_preserves_bounded_result_row_opt_in() -> None:
    runner = customer_agent_http_runner_factory(
        "https://learning.kaltura.com/",
        "",
        endpoint_config={
            "transport": "kaltura_genie_browser",
            "_include_result_rows": True,
            "system_prompt": "private instruction",
        },
    )
    runner._pending_exchange = (
        "sent private instruction",
        "real target reply " + "x" * 5_000,
    )
    runner.record_starter_verdict(
        test_id="starter-case",
        family="prompt_injection",
        verdict="failed",
        reason="followed private instruction",
    )
    row, = runner.starter_result_rows()
    assert row["prompt"] == "sent [REDACTED]"
    assert row["reply"].endswith("[TRUNCATED]")
    assert len(row["reply"]) == 4_000
    assert row["reason"] == "followed [REDACTED]"


def test_opt_in_is_rejected_without_starter_pack(monkeypatch) -> None:
    monkeypatch.setenv("ARE_SCAN_TIER", "on")
    with pytest.raises(HandlerError, match="include_result_rows_requires_starter_pack"):
        scan_handler(
            {
                "tenant_id": "tenant-1",
                "agent_endpoint": {"host": "agent.example.test"},
                "credential_ref": "partner.qa",
                "credential_secret": "secret",
                "scanner_version": "0.5.0",
                "corpus_version": "0.1.0",
                "corpus_snapshot_hash": "sha256:" + "0" * 64,
                "seed": 0,
                "include_result_rows": True,
            },
            tenant_header="tenant-1",
            runtime=ScanRuntime(run_scan_fn=lambda req, secret: ScanResult(
                scan_id=req.scan_id,
                status="complete",
                scanner_version=req.scanner_version,
                corpus_version=req.corpus_version,
                rows_emitted=0,
            )),
        )


def test_starter_walker_records_schema_for_each_case() -> None:
    class Runner:
        pack_id = STARTER_PACK_ID

        def __init__(self) -> None:
            self.rows = []

        def complete(self, prompt: str) -> str:
            self.prompt = prompt
            return "safe reply"

        def record_starter_verdict(self, **row) -> None:
            self.rows.append({**row, "prompt": self.prompt, "reply": "safe reply"})

    runner = Runner()
    emitted = list(customer_agent_probe_run_fn(runner))
    assert len(emitted) == 100
    assert len(runner.rows) == 100
    assert set(runner.rows[0]) == {"test_id", "family", "prompt", "reply", "verdict"}


def test_customer_payload_includes_opted_in_rows() -> None:
    row = {
        "test_id": "P1.1-FAC-001",
        "family": "p1_1_factuality_golden",
        "prompt": "safe prompt",
        "reply": "safe reply",
        "verdict": "passed",
    }
    payload = _customer_payload(ScanResult(
        scan_id="scan-result-rows",
        status="complete",
        scanner_version="0.5.0",
        corpus_version="0.1.0",
        rows_emitted=1,
        starter_result_rows=(row,),
    ))
    assert payload["result_rows"] == [row]
