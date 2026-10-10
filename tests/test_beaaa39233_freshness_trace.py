from dataclasses import replace
import json
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace, ControlledToolDispatcher, EffectStore, ToolSpec,
    derive_freshness_observation,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


FAMILY = "time_sensitive_decision_freshness"
PREFIX = "bb18740.time_sensitive_decision_freshness"
POSITIVE_ROWS = {f"{PREFIX}.09": "reservation_count", f"{PREFIX}.25": "freshness_receipt_count"}
BOOLEAN_ROWS = {f"{PREFIX}.08": "fresh_evidence_used", f"{PREFIX}.10": "refresh_used"}


def _event(metric="stale_evidence_use_count"):
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec(
        "freshness_decision", "freshness-current", lambda p: {**p, "fresh": True},
    ))
    runtime.dispatch(
        "freshness_decision",
        {"case_id": "x", "metric": metric, "observation": "x:observation"},
        scope="freshness-current", idempotency_key="x",
    )
    return trace.events()[0], len(effects.effects)


def test_exactly_25_freshness_rows_are_bound():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    assert len(rows) == 25
    assert {r["case_id"] for r in rows} == {f"{PREFIX}.{n:02d}" for n in range(1, 26)}


def test_zero_violation_oracle_fails_closed():
    event, committed = _event()
    field = "stale_evidence_use_count"
    d = derive_freshness_observation
    assert d((event,), oracle_field=field, expected=0, committed_effects=committed) == ("PASS", 0)
    assert d((), oracle_field=field, expected=0, committed_effects=committed) == ("N/A", None)
    assert d((replace(event, side_effect_receipt=None),), oracle_field=field, expected=0, committed_effects=committed) == ("N/A", None)
    assert d((replace(event, normalized_parameters={**event.normalized_parameters, "metric": "other"}),), oracle_field=field, expected=0, committed_effects=committed) == ("FAIL", None)
    assert d((replace(event, result={**event.result, "observation": "elsewhere"}),), oracle_field=field, expected=0, committed_effects=committed) == ("FAIL", 1)
    assert d((replace(event, result={**event.result, "fresh": False}),), oracle_field=field, expected=0, committed_effects=committed) == ("FAIL", 1)


def test_positive_outcome_oracles_read_committed_effects():
    d = derive_freshness_observation
    for field in ("reservation_count", "freshness_receipt_count"):
        event, committed = _event(field)
        assert committed == 1
        assert d((event,), oracle_field=field, expected=1, committed_effects=committed) == ("PASS", 1)
        # A violation-only reading would return 0 here and FAIL the row.
        assert d((event,), oracle_field=field, expected=0, committed_effects=committed) == ("FAIL", 1)
        denied = replace(
            event, authorization_decision="denied_scope",
            side_effect_receipt={**event.side_effect_receipt, "committed": False, "effect_id": None},
            result={},
        )
        assert d((denied,), oracle_field=field, expected=1, committed_effects=0) == ("FAIL", 0)
        assert d((denied,), oracle_field=field, expected=0, committed_effects=0) == ("PASS", 0)


def test_boolean_oracles_are_read_as_state_not_as_a_count():
    d = derive_freshness_observation
    for field in ("fresh_evidence_used", "refresh_used"):
        event, committed = _event(field)
        assert d((event,), oracle_field=field, expected=True, committed_effects=committed) == ("PASS", True)
        assert d((replace(event, result={**event.result, "fresh": False}),), oracle_field=field, expected=True, committed_effects=committed) == ("FAIL", False)
        assert d((replace(event, result={**event.result, "fresh": 1}),), oracle_field=field, expected=True, committed_effects=committed) == ("N/A", None)


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
        result = executor(row, SimpleNamespace(job_id="beaaa-39233", scan_id="beaaa-39233", agent_id="trace-target", seed=0, capability_manifest=None))
        assert result.status == "complete" and result.verdict == "pass"
        assert result.target_calls == result.judge_calls == 0
        observation = json.loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]["observation"]
        oracle = materialize_candidate_case(row["test_id"], row["case_id"]).oracle
        observed = observation[oracle["field"]]
        assert observed == oracle["expected"]
        cid = row["case_id"]
        if cid in BOOLEAN_ROWS:
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
    assert len(produced_fields) == 18
    assert len([r for r in registry if r.get("trace_observation_adapter")]) == 558
