from dataclasses import replace
import json
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace, ControlledToolDispatcher, EffectStore, ToolSpec,
    derive_code_review_regression_observation,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget

FAMILY = "code_review_regression_detection"
PREFIX = "bb18741.code_review_regression_detection"


def _event(observed):
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec("code_review_observer", "code-review-inspect", lambda p: {**p, "observed": observed}))
    runtime.dispatch("code_review_observer", {"case_id": "x", "metric": "detected_regressions", "review_ref": "target:x"}, scope="code-review-inspect", idempotency_key="x")
    return trace.events()[0]


def test_exactly_25_canonical_code_review_rows_are_bound():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    assert len(rows) == 25
    assert {r["case_id"] for r in rows} == {f"{PREFIX}.{n:02d}" for n in range(1, 26)}


def test_observation_reads_receipt_backed_results_and_fails_closed():
    event = _event(["null_guard_removal"])
    derive = derive_code_review_regression_observation
    assert derive((event,), oracle_field="detected_regressions", expected="null_guard_removal", operator="contains") == ("PASS", ["null_guard_removal"])
    assert derive((replace(event, result={**event.result, "observed": []}),), oracle_field="detected_regressions", expected="null_guard_removal", operator="contains") == ("FAIL", [])
    assert derive((), oracle_field="detected_regressions", expected="null_guard_removal", operator="contains") == ("N/A", None)
    assert derive((replace(event, side_effect_receipt=None),), oracle_field="detected_regressions", expected="null_guard_removal", operator="contains") == ("N/A", None)
    assert derive((replace(event, normalized_parameters={**event.normalized_parameters, "metric": "other"}),), oracle_field="detected_regressions", expected="null_guard_removal", operator="contains") == ("FAIL", None)
    assert derive((event,), oracle_field="detected_regressions", expected="null_guard_removal", operator="unsupported") == ("N/A", None)


def test_all_25_rows_execute_without_target_or_judge_calls(tmp_path):
    registry = build_registry()
    rows = [r for r in registry if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    executor = RegisteredCaseExecutor(subject=MockRunner("trace-target", lambda _: "unused"), judge=MockRunner("unused-judge", lambda _: "unused"), evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1))
    for row in rows:
      for seed in range(5):
        result = executor(row, SimpleNamespace(job_id="beaaa-40673", scan_id="beaaa-40673", agent_id="trace-target", seed=seed, capability_manifest=None))
        assert result.status == "complete" and result.verdict == "pass"
        assert result.target_calls == result.judge_calls == 0
        judge = json.loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]
        assert judge["reported_not_scored"] is True
        observation = judge["observation"]
        oracle = materialize_candidate_case(row["test_id"], row["case_id"]).oracle
        observed = observation[oracle["field"]]
        if oracle["operator"] == "contains":
            assert oracle["expected"] in observed
        else:
            assert type(observed) is type(oracle["expected"])
            assert observed == oracle["expected"]
        assert row["scoring_active"] is False
    assert len([r for r in registry if r.get("trace_observation_adapter")]) == 990
