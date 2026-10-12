from dataclasses import replace
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    TraceEvent,
    derive_approval_freshness_replay,
    execute_approval_freshness_replay_case,
)
from tools.active_test_registry import build_registry
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


FAMILY = "approval_freshness_replay"
CASE_IDS = {
    "bb18716.approval_freshness_replay.09",
    "bb18716.approval_freshness_replay.24",
}


def _rows():
    return [row for row in build_registry() if row.get("trace_observation_adapter") and row["test_id"] == FAMILY]


def test_registry_binds_exactly_two_rows_without_spillover():
    rows = _rows()
    assert {row["case_id"] for row in rows} == CASE_IDS
    assert len([row for row in build_registry() if row.get("trace_observation_adapter")]) >= 800


def test_replay_is_rejected_and_fresh_control_commits():
    fields = {"bb18716.approval_freshness_replay.09": "delete_count", "bb18716.approval_freshness_replay.24": "write_count"}
    for case_id, field in fields.items():
        record = execute_approval_freshness_replay_case(
            case_id=case_id, oracle_field=field, expected=0, scanner_version="test",
            corpus_version="test", target_identity="test", model_versions={"target": "test"}, seed=0,
        )
        assert record["verdict"] == "PASS"
        assert record[field] == 0
        assert record["attempt_count"] == 2
        assert record["committed_effect_count"] == 1
        assert len(record["approval_ledger"]) == len(record["committed_state"]) == 1
        events = tuple(TraceEvent(**event) for event in record["events"])
        assert derive_approval_freshness_replay(events, oracle_field=field) == ("PASS", 0)
        assert derive_approval_freshness_replay((replace(events[0], side_effect_receipt=None), events[1]), oracle_field=field)[0] != "PASS"


def test_registered_rows_execute_without_target_or_judge_calls(tmp_path):
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: "unused"),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1),
    )
    context = SimpleNamespace(job_id="beaaa-39660", scan_id="beaaa-39660", agent_id="trace-target", seed=0, capability_manifest={"approval_binding": True})
    results = [executor(row, context) for row in _rows()]
    assert len(results) == 2
    assert {result.status for result in results} == {"complete"}
    assert {result.verdict for result in results} == {"pass"}
    assert all(result.target_calls == result.judge_calls == 0 for result in results)
