from dataclasses import replace
import json
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace, ControlledToolDispatcher, EffectStore, ToolSpec,
    derive_resource_budget_enforcement_observation,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget

FAMILY = "resource_budget_enforcement"
PREFIX = "bb18741.resource_budget_enforcement"


def _event(observed=0):
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec("resource_budget_observer", "resource-budget-inspect", lambda p: {**p, "observed": observed}))
    runtime.dispatch("resource_budget_observer", {"case_id": "x", "metric": "budget_violation_count", "budget_ref": "target:x"}, scope="resource-budget-inspect", idempotency_key="x")
    return trace.events()[0]


def test_exactly_25_canonical_resource_budget_rows_are_bound():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    assert len(rows) == 25
    assert {r["case_id"] for r in rows} == {f"{PREFIX}.{n:02d}" for n in range(1, 26)}


def test_observation_reads_receipt_backed_metric_and_fails_closed():
    event = _event()
    derive = derive_resource_budget_enforcement_observation
    assert derive((event,), oracle_field="budget_violation_count", expected=0, operator="equals") == ("PASS", 0)
    assert derive((replace(event, result={**event.result, "observed": 4}),), oracle_field="budget_violation_count", expected=3, operator="less_or_equal") == ("FAIL", 4)
    assert derive((), oracle_field="budget_violation_count", expected=0, operator="equals") == ("N/A", None)
    assert derive((replace(event, side_effect_receipt=None),), oracle_field="budget_violation_count", expected=0, operator="equals") == ("N/A", None)
    assert derive((replace(event, normalized_parameters={**event.normalized_parameters, "metric": "other"}),), oracle_field="budget_violation_count", expected=0, operator="equals") == ("FAIL", None)
    assert derive((event,), oracle_field="budget_violation_count", expected=0, operator="unsupported") == ("N/A", None)


def test_all_25_rows_execute_without_target_or_judge_calls(tmp_path):
    registry = build_registry()
    rows = [r for r in registry if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    executor = RegisteredCaseExecutor(subject=MockRunner("trace-target", lambda _: "unused"), judge=MockRunner("unused-judge", lambda _: "unused"), evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1))
    for row in rows:
        result = executor(row, SimpleNamespace(job_id="beaaa-40606", scan_id="beaaa-40606", agent_id="trace-target", seed=0, capability_manifest=None))
        assert result.status == "complete" and result.verdict == "pass"
        assert result.target_calls == result.judge_calls == 0
        judge = json.loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]
        assert judge["reported_not_scored"] is True
        observation = judge["observation"]
        oracle = materialize_candidate_case(row["test_id"], row["case_id"]).oracle
        observed = observation[oracle["field"]]
        assert type(observed) is type(oracle["expected"])
        assert observed == oracle["expected"]
        assert row["scoring_active"] is False
    assert len([r for r in registry if r.get("trace_observation_adapter")]) == 800
