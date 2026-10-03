from dataclasses import replace
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace,
    ControlledToolDispatcher,
    EffectStore,
    ToolSpec,
    derive_tool_schema_observation,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _event():
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec("schema_operation", "schema-current", lambda p: dict(p)))
    runtime.dispatch(
        "schema_operation",
        {"case_id": "x", "metric": "effect_count", "observed_value": 1},
        scope="schema-current",
        idempotency_key="x",
    )
    return trace.events()[0]


def test_exactly_25_tool_schema_rows_are_bound():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and r["test_id"] == "tool_schema_drift_partial_failure"]
    assert len(rows) == 25
    assert {r["case_id"] for r in rows} == {f"bb18719.tool_schema_drift_partial_failure.{n:02d}" for n in range(1, 26)}


def test_missing_malformed_and_contradictory_trace_evidence_fails_closed():
    event = _event()
    assert derive_tool_schema_observation((event,), oracle_field="effect_count", expected_value=1) == ("PASS", 1)
    assert derive_tool_schema_observation((replace(event, side_effect_receipt=None),), oracle_field="effect_count", expected_value=1) == ("N/A", None)
    assert derive_tool_schema_observation((replace(event, normalized_parameters={**event.normalized_parameters, "metric": "tool_call_count"}),), oracle_field="effect_count", expected_value=1) == ("FAIL", None)
    assert derive_tool_schema_observation((replace(event, result={**event.result, "observed_value": 0}),), oracle_field="effect_count", expected_value=1) == ("FAIL", None)


def test_all_25_rows_execute_and_emit_family_fields(tmp_path):
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and r["test_id"] == "tool_schema_drift_partial_failure"]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: '{"effect_count":999}'),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=1),
    )
    produced = set()
    for row in rows:
        result = executor(row, SimpleNamespace(job_id="beaaa-38011", scan_id="beaaa-38011", agent_id="trace-target", seed=0, capability_manifest=None))
        assert result.status == "complete" and result.verdict == "pass"
        assert result.target_calls == result.judge_calls == 0
        observation = __import__("json").loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]["observation"]
        oracle = materialize_candidate_case(row["test_id"], row["case_id"]).oracle
        assert observation[oracle["field"]] == oracle["expected"]
        produced.add(oracle["field"])
    assert produced == {"tool_call_count", "followup_effect_count", "dependent_call_count", "completed_operation_count", "duplicate_effect_count", "effect_count", "terminal_state", "complete_result_claim_count", "completion_count", "transition_count", "parsed_response_count", "final_claim_count", "success_count", "success_claim_count", "stale_schema_call_count"}
