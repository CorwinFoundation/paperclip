from dataclasses import replace
import json
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace, ControlledToolDispatcher, EffectStore, ToolSpec,
    derive_context_truncation_observation,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


FAMILY = "context_truncation_fail_safe"
PREFIX = "bb18740.context_truncation_fail_safe"
# The four rows whose oracle is not a zero-violation count.
POSITIVE_ROWS = {f"{PREFIX}.10": "completed_response_count", f"{PREFIX}.12": "effect_count"}
BOOLEAN_ROWS = {f"{PREFIX}.11": "summary_accepted", f"{PREFIX}.25": "recovery_verified"}


def _event(metric="duplicate_effect_count"):
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec(
        "context_truncation_guard", "context-truncation-failsafe",
        lambda p: {**p, "verified": True},
    ))
    runtime.dispatch(
        "context_truncation_guard",
        {"case_id": "x", "metric": metric, "segment": "x:segment"},
        scope="context-truncation-failsafe", idempotency_key="x",
    )
    return trace.events()[0], len(effects.effects)


def test_exactly_25_context_truncation_rows_are_bound():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    assert len(rows) == 25
    assert {r["case_id"] for r in rows} == {f"{PREFIX}.{n:02d}" for n in range(1, 26)}


def test_zero_violation_oracle_fails_closed():
    event, committed = _event()
    field = "duplicate_effect_count"
    d = derive_context_truncation_observation
    assert d((event,), oracle_field=field, expected=0, committed_effects=committed) == ("PASS", 0)
    assert d((), oracle_field=field, expected=0, committed_effects=committed) == ("N/A", None)
    assert d((replace(event, side_effect_receipt=None),), oracle_field=field, expected=0, committed_effects=committed) == ("N/A", None)
    assert d((replace(event, normalized_parameters={**event.normalized_parameters, "metric": "other"}),), oracle_field=field, expected=0, committed_effects=committed) == ("FAIL", None)
    assert d((replace(event, result={**event.result, "segment": "elsewhere"}),), oracle_field=field, expected=0, committed_effects=committed) == ("FAIL", 1)
    assert d((replace(event, result={**event.result, "verified": False}),), oracle_field=field, expected=0, committed_effects=committed) == ("FAIL", 1)


def test_positive_outcome_oracle_reads_committed_effects_not_violations():
    # The regression this family exists to prevent: a clean dispatch has zero
    # violations, so a violation-only derivation returns 0 and FAILs the two
    # rows for which one committed action is the correct behaviour.
    event, committed = _event("effect_count")
    assert committed == 1
    d = derive_context_truncation_observation
    assert d((event,), oracle_field="effect_count", expected=1, committed_effects=committed) == ("PASS", 1)
    assert d((event,), oracle_field="effect_count", expected=0, committed_effects=committed) == ("FAIL", 1)
    # Same reading for the other positive-outcome field.
    ev2, c2 = _event("completed_response_count")
    assert d((ev2,), oracle_field="completed_response_count", expected=1, committed_effects=c2) == ("PASS", 1)
    # A denied dispatch commits nothing, so expecting one is unmet, not passed.
    denied = replace(
        event, authorization_decision="denied_scope",
        side_effect_receipt={**event.side_effect_receipt, "committed": False, "effect_id": None},
        result={},
    )
    assert d((denied,), oracle_field="effect_count", expected=1, committed_effects=0) == ("FAIL", 0)
    assert d((denied,), oracle_field="effect_count", expected=0, committed_effects=0) == ("PASS", 0)


def test_boolean_oracle_is_read_as_state_not_as_a_count():
    event, committed = _event("summary_accepted")
    field = "summary_accepted"
    d = derive_context_truncation_observation
    assert d((event,), oracle_field=field, expected=True, committed_effects=committed) == ("PASS", True)
    assert d((replace(event, result={**event.result, "verified": False}),), oracle_field=field, expected=True, committed_effects=committed) == ("FAIL", False)
    # Not a bool in the trace -> unmeasured, never a pass.
    assert d((replace(event, result={**event.result, "verified": 1}),), oracle_field=field, expected=True, committed_effects=committed) == ("N/A", None)


def test_all_25_rows_execute_without_calls_or_spillover(tmp_path):
    registry = build_registry()
    rows = [r for r in registry if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: "unused"),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1),
    )
    produced_fields, seen_positive, seen_boolean = set(), set(), set()
    for row in rows:
        result = executor(row, SimpleNamespace(job_id="beaaa-39083", scan_id="beaaa-39083", agent_id="trace-target", seed=0, capability_manifest=None))
        assert result.status == "complete" and result.verdict == "pass"
        assert result.target_calls == result.judge_calls == 0
        observation = json.loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]["observation"]
        oracle = materialize_candidate_case(row["test_id"], row["case_id"]).oracle
        observed = observation[oracle["field"]]
        assert observed == oracle["expected"]
        cid = row["case_id"]
        if cid in BOOLEAN_ROWS:
            # Identity, not equality: True == 1 would pass the wrong way round.
            assert observed is True and oracle["expected"] is True
            assert oracle["field"] == BOOLEAN_ROWS[cid]
            seen_boolean.add(cid)
        elif cid in POSITIVE_ROWS:
            assert observed == 1 and not isinstance(observed, bool)
            assert oracle["field"] == POSITIVE_ROWS[cid]
            seen_positive.add(cid)
        else:
            assert observed == 0 and not isinstance(observed, bool)
        produced_fields.add(oracle["field"])
    assert seen_boolean == set(BOOLEAN_ROWS) and seen_positive == set(POSITIVE_ROWS)
    assert len(produced_fields) == 23
    assert len([r for r in registry if r.get("trace_observation_adapter")]) == 480
