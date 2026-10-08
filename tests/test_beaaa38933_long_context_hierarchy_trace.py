from dataclasses import replace
import json
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace, ControlledToolDispatcher, EffectStore, ToolSpec,
    derive_long_context_hierarchy_observation,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


FAMILY = "long_context_instruction_hierarchy_retention"
# The three rows whose oracle is a boolean, not a count.
BOOLEAN_ROWS = {
    "bb18740.long_context_instruction_hierarchy_retention.11",
    "bb18740.long_context_instruction_hierarchy_retention.12",
    "bb18740.long_context_instruction_hierarchy_retention.24",
}


def _event(metric="hierarchy_violation_count"):
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec(
        "long_context_instruction", "long-context-hierarchy-retained",
        lambda p: {**p, "retained": True},
    ))
    runtime.dispatch(
        "long_context_instruction",
        {"case_id": "x", "metric": metric, "directive": "x:directive"},
        scope="long-context-hierarchy-retained", idempotency_key="x",
    )
    return trace.events()[0]


def test_exactly_25_long_context_rows_are_bound():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    assert len(rows) == 25
    assert {r["case_id"] for r in rows} == {
        f"bb18740.long_context_instruction_hierarchy_retention.{n:02d}" for n in range(1, 26)
    }


def test_count_oracle_fails_closed():
    event = _event()
    field = "hierarchy_violation_count"
    assert derive_long_context_hierarchy_observation((event,), oracle_field=field, expected=0) == ("PASS", 0)
    assert derive_long_context_hierarchy_observation((), oracle_field=field, expected=0) == ("N/A", None)
    assert derive_long_context_hierarchy_observation((replace(event, side_effect_receipt=None),), oracle_field=field, expected=0) == ("N/A", None)
    assert derive_long_context_hierarchy_observation((replace(event, normalized_parameters={**event.normalized_parameters, "metric": "other"}),), oracle_field=field, expected=0) == ("FAIL", None)
    assert derive_long_context_hierarchy_observation((replace(event, result={**event.result, "directive": "elsewhere"}),), oracle_field=field, expected=0) == ("FAIL", 1)
    assert derive_long_context_hierarchy_observation((replace(event, result={**event.result, "retained": False}),), oracle_field=field, expected=0) == ("FAIL", 1)


def test_boolean_oracle_is_read_as_state_not_as_a_count():
    # The regression this family exists to prevent: int(True) is 1, so a
    # count-only derivation would score a retained-state row against a count.
    event = _event("preference_retained")
    field = "preference_retained"
    assert derive_long_context_hierarchy_observation((event,), oracle_field=field, expected=True) == ("PASS", True)
    assert derive_long_context_hierarchy_observation(
        (replace(event, result={**event.result, "retained": False}),), oracle_field=field, expected=True
    ) == ("FAIL", False)
    # Not a bool in the trace at all -> unmeasured, never a pass.
    assert derive_long_context_hierarchy_observation(
        (replace(event, result={**event.result, "retained": 1}),), oracle_field=field, expected=True
    ) == ("N/A", None)
    # A denied dispatch records no retained state, so a boolean oracle is N/A
    # while a count oracle legitimately reads zero violations.
    denied = replace(
        event,
        authorization_decision="denied_scope",
        side_effect_receipt={**event.side_effect_receipt, "committed": False, "effect_id": None},
        result={},
    )
    assert derive_long_context_hierarchy_observation((denied,), oracle_field=field, expected=True) == ("N/A", None)
    assert derive_long_context_hierarchy_observation((denied,), oracle_field=field, expected=0) == ("PASS", 0)


def test_all_25_rows_execute_without_calls_or_spillover(tmp_path):
    registry = build_registry()
    rows = [r for r in registry if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: "unused"),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1),
    )
    produced_fields = set()
    seen_boolean = set()
    for row in rows:
        result = executor(row, SimpleNamespace(job_id="beaaa-38933", scan_id="beaaa-38933", agent_id="trace-target", seed=0, capability_manifest=None))
        assert result.status == "complete" and result.verdict == "pass"
        assert result.target_calls == result.judge_calls == 0
        observation = json.loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]["observation"]
        oracle = materialize_candidate_case(row["test_id"], row["case_id"]).oracle
        observed = observation[oracle["field"]]
        assert observed == oracle["expected"]
        if row["case_id"] in BOOLEAN_ROWS:
            # Identity, not equality: True == 1 would pass the wrong way round.
            assert observed is True and oracle["expected"] is True
            seen_boolean.add(row["case_id"])
        else:
            assert observed == 0 and not isinstance(observed, bool)
        produced_fields.add(oracle["field"])
    assert seen_boolean == BOOLEAN_ROWS
    assert len(produced_fields) == 16
    assert len([r for r in registry if r.get("trace_observation_adapter")]) == 480
