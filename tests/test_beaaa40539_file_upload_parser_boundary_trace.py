from dataclasses import replace
import json
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace, ControlledToolDispatcher, EffectStore, ToolSpec,
    derive_file_upload_parser_boundary_observation,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget

FAMILY = "file_upload_parser_boundary"
PREFIX = "bb18717.file_upload_parser_boundary"


def _event(observed=0):
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec("file_parser_observer", "file-parser-inspect", lambda p: {**p, "observed": observed}))
    runtime.dispatch("file_parser_observer", {"case_id": "x", "metric": "accepted_file_count", "artifact_ref": "fixture:x"}, scope="file-parser-inspect", idempotency_key="x")
    return trace.events()[0]


def test_exactly_24_canonical_file_parser_rows_are_bound():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    assert len(rows) == 24
    assert {r["case_id"] for r in rows} == {f"{PREFIX}.{n:02d}" for n in range(1, 25)}


def test_observation_reads_receipt_backed_metric_and_fails_closed():
    event = _event()
    derive = derive_file_upload_parser_boundary_observation
    assert derive((event,), oracle_field="accepted_file_count", expected=0) == ("PASS", 0)
    assert derive((), oracle_field="accepted_file_count", expected=0) == ("N/A", None)
    assert derive((replace(event, side_effect_receipt=None),), oracle_field="accepted_file_count", expected=0) == ("N/A", None)
    assert derive((replace(event, normalized_parameters={**event.normalized_parameters, "metric": "other"}),), oracle_field="accepted_file_count", expected=0) == ("FAIL", None)
    assert derive((replace(event, result={**event.result, "observed": False}),), oracle_field="accepted_file_count", expected=0) == ("N/A", None)


def test_all_24_rows_execute_without_target_or_judge_calls(tmp_path):
    registry = build_registry()
    rows = [r for r in registry if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    executor = RegisteredCaseExecutor(subject=MockRunner("trace-target", lambda _: "unused"), judge=MockRunner("unused-judge", lambda _: "unused"), evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1))
    for row in rows:
        result = executor(row, SimpleNamespace(job_id="beaaa-40539", scan_id="beaaa-40539", agent_id="trace-target", seed=0, capability_manifest=None))
        assert result.status == "complete" and result.verdict == "pass"
        assert result.target_calls == result.judge_calls == 0
        judge = json.loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]
        assert judge["reported_not_scored"] is True
        observation = judge["observation"]
        oracle = materialize_candidate_case(row["test_id"], row["case_id"]).oracle
        observed = observation[oracle["field"]]
        assert observed == oracle["expected"] and type(observed) is type(oracle["expected"])
        assert row["scoring_active"] is False
    assert len([r for r in registry if r.get("trace_observation_adapter")]) == 775
