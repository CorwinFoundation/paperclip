from dataclasses import replace
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import derive_approval_binding, execute_approval_binding_case
from tools.active_test_registry import build_registry
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


FAMILY = "approval_preview_mutation_binding"


def _rows():
    return [row for row in build_registry() if row.get("trace_observation_adapter") and row["test_id"] == FAMILY]


def test_registry_marks_exactly_the_25_approval_binding_rows_without_spillover():
    rows = _rows()
    assert len(rows) == 25
    assert {row["case_id"] for row in rows} == {
        f"bb18742.{FAMILY}.{n:02d}" for n in range(1, 26)
    }


def test_each_row_produces_one_distinct_zero_valued_oracle_field():
    records = [execute_approval_binding_case(
        case_id=row["case_id"], scanner_version="test", corpus_version="test",
        target_identity="test", model_versions={"target": "test"}, seed=0,
    ) for row in _rows()]
    assert {record["verdict"] for record in records} == {"PASS"}
    assert len({record["produced_field"] for record in records}) == 25
    assert all(record[record["produced_field"]] == 0 for record in records)


def test_nonzero_contradiction_and_missing_or_reordered_evidence_fail_closed():
    record = execute_approval_binding_case(
        case_id=f"bb18742.{FAMILY}.01", scanner_version="test",
        corpus_version="test", target_identity="test",
        model_versions={"target": "test"}, seed=0,
    )
    from harness.trace_observation import TraceEvent
    event = TraceEvent(**record["events"][0])
    bad = replace(event, result={**event.result, "approved_payload_mismatch_count": 1})
    assert derive_approval_binding((bad,)) == "FAIL"
    assert derive_approval_binding((replace(event, side_effect_receipt=None),)) == "N/A"
    assert derive_approval_binding((replace(event, sequence=1),)) == "N/A"


def test_all_registered_rows_execute_without_target_or_judge_calls(tmp_path):
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: "unused"),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1),
    )
    context = SimpleNamespace(
        job_id="beaaa-38146", scan_id="beaaa-38146", agent_id="trace-target",
        seed=0, capability_manifest={"approval_binding": True},
    )
    results = [executor(row, context) for row in _rows()]
    assert len(results) == 25
    assert {result.status for result in results} == {"complete"}
    assert {result.verdict for result in results} == {"pass"}
    assert all(result.target_calls == result.judge_calls == 0 for result in results)
