"""BEAAA-30156: bind the active PII-attestation row to live dispatch."""
from __future__ import annotations

from harness.runner import MockRunner
from tools.active_test_registry import build_registry, count_active_executable
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _row() -> dict:
    rows = [
        row
        for row in build_registry()
        if row.get("status") == "active" and row["test_id"] == "pii_attestation"
    ]
    assert len(rows) == 1
    return rows[0]


def test_exact_pii_attestation_roster_is_executable() -> None:
    row = _row()
    assert row["case_id"] == "pii_attestation.evidence_pack"
    assert row["runner"] == "governance_attestation_worker"
    assert row["production_runner_executable"] is True
    assert row["scoring_active"] is True
    assert row["reporting_status"] == "scored"
    assert count_active_executable(build_registry()) == 2_350


def test_live_dispatch_records_one_verdict_and_one_target_call(tmp_path) -> None:
    calls: list[str] = []

    def target(prompt: str) -> str:
        calls.append(prompt)
        return (
            "Our PII controls include a signed DPA, DPIA, DSAR runbook, "
            "72-hour breach notification, and a current third-party audit."
        )

    executor = RegisteredCaseExecutor(
        subject=MockRunner("authorized-reference-target", target),
        judge=MockRunner("judge", lambda _prompt: '{"verdict":"pass","score":1.0}'),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(),
    )
    outcome = executor._execute_case(_row()["test_id"], _row()["case_id"], _row())

    assert len(calls) == executor.ledger.snapshot()["target_calls"] == 1
    assert executor.ledger.snapshot()["judge_calls"] == 1
    assert outcome["verdict"] == "pass"
    assert outcome["payloads"] and outcome["responses"]
    assert outcome["judge"]["type"] == "pii_attestation_registered_adapter"
