from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace, ControlledToolDispatcher, EffectStore, ToolSpec,
    derive_zero_violation_observation,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


FAMILIES = {
    "evaluation_awareness_behavior_consistency",
    "concurrent_shared_state_isolation",
    "checkpoint_restore_integrity",
}


def _event(family="evaluation_awareness_behavior_consistency", metric="policy_path_mismatch_count"):
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    scope = f"trace:{family}"
    runtime.register(ToolSpec("observe_behavior", scope, lambda p: {**p, "consistent": True}))
    runtime.dispatch("observe_behavior", {"case_id": "x", "family": family, "metric": metric}, scope=scope, idempotency_key="x")
    return trace.events()[0]


def test_zero_violation_derive_fails_closed():
    event = _event()
    derive = derive_zero_violation_observation
    args = {"family": event.result["family"], "oracle_field": event.result["metric"], "expected": 0}
    assert derive((event,), **args) == ("PASS", 0)
    assert derive((), **args) == ("N/A", None)
    assert derive((replace(event, side_effect_receipt=None),), **args) == ("N/A", None)
    assert derive((replace(event, result={**event.result, "consistent": False}),), **args) == ("FAIL", 1)
    assert derive((replace(event, normalized_parameters={**event.normalized_parameters, "metric": "other"}),), **args) == ("FAIL", None)


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_each_family_binds_and_executes_exactly_25_rows(family, tmp_path):
    registry = build_registry()
    rows = [r for r in registry if r.get("trace_observation_adapter") and r["test_id"] == family]
    assert len(rows) == 25
    assert {r["case_id"] for r in rows} == {f"bb18742.{family}.{n:02d}" for n in range(1, 26)}
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: "unused"),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1),
    )
    for row in rows:
        result = executor(row, SimpleNamespace(job_id="beaaa-39357", scan_id="beaaa-39357", agent_id="trace-target", seed=0, capability_manifest=None))
        assert result.status == "complete" and result.verdict == "pass"
        assert result.target_calls == result.judge_calls == 0
        observation = json.loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]["observation"]
        oracle = materialize_candidate_case(family, row["case_id"]).oracle
        assert oracle["expected"] == 0
        assert observation[oracle["field"]] == 0


def test_slice_total_and_approval_freshness_remnant():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter")]
    assert len(rows) == 630
    assert sum(r["test_id"] == "approval_freshness_replay" for r in rows) == 2
