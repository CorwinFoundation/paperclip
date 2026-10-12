from dataclasses import replace
import json
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace, ControlledToolDispatcher, EffectStore, ToolSpec,
    derive_browser_surface_action_integrity_observation,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


FAMILY = "browser_surface_action_integrity"
PREFIX = "bb18716.browser_surface_action_integrity"
NONZERO_ROWS = {f"{PREFIX}.{n:02d}" for n in (1, 9, 13, 15, 17, 24)}


def _event(metric="click_count", observed=1):
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec(
        "browser_surface_action", "browser-action-authorized",
        lambda p: {**p, "observed": observed},
    ))
    runtime.dispatch(
        "browser_surface_action",
        {"case_id": "x", "metric": metric, "surface_ref": "x:surface"},
        scope="browser-action-authorized", idempotency_key="x",
    )
    return trace.events()[0]


def test_exactly_24_canonical_browser_surface_rows_are_bound():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    assert len(rows) == 24
    assert {r["case_id"] for r in rows} == {f"{PREFIX}.{n:02d}" for n in range(1, 25)}


def test_observation_reads_recorded_outcome_and_fails_closed():
    event = _event()
    derive = derive_browser_surface_action_integrity_observation
    assert derive((event,), oracle_field="click_count", expected=1) == ("PASS", 1)
    string_event = replace(event, result={**event.result, "observed": "genuine_submit"})
    assert derive((string_event,), oracle_field="click_count", expected="genuine_submit") == ("PASS", "genuine_submit")
    assert derive((), oracle_field="click_count", expected=1) == ("N/A", None)
    assert derive((replace(event, side_effect_receipt=None),), oracle_field="click_count", expected=1) == ("N/A", None)
    assert derive((replace(event, normalized_parameters={**event.normalized_parameters, "metric": "other"}),), oracle_field="click_count", expected=1) == ("FAIL", None)
    assert derive((replace(event, result={**event.result, "observed": True}),), oracle_field="click_count", expected=1) == ("N/A", None)


def test_denied_dispatch_records_zero_without_an_effect():
    event = _event()
    denied = replace(
        event, authorization_decision="denied_scope",
        side_effect_receipt={"committed": False, "reason": "scope_denied", "effect_id": None},
        result={"ok": False, "error": "cross_scope_write_denied"},
    )
    derive = derive_browser_surface_action_integrity_observation
    assert derive((denied,), oracle_field="click_count", expected=0) == ("PASS", 0)
    assert derive((denied,), oracle_field="click_count", expected=1) == ("FAIL", 0)


def test_all_24_rows_execute_without_calls_scoring_or_spillover(tmp_path):
    registry = build_registry()
    rows = [r for r in registry if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: "unused"),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1),
    )
    nonzero = set()
    for row in rows:
        result = executor(row, SimpleNamespace(job_id="beaaa-40376", scan_id="beaaa-40376", agent_id="trace-target", seed=0, capability_manifest=None))
        assert result.status == "complete" and result.verdict == "pass"
        assert result.target_calls == result.judge_calls == 0
        judge = json.loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]
        assert judge["reported_not_scored"] is True
        observation = judge["observation"]
        oracle = materialize_candidate_case(row["test_id"], row["case_id"]).oracle
        observed = observation[oracle["field"]]
        assert observed == oracle["expected"] and type(observed) is type(oracle["expected"])
        assert row["scoring_active"] is False
        assert row["reporting_status"] == "reported_not_scored"
        if observed != 0:
            nonzero.add(row["case_id"])
    assert nonzero == NONZERO_ROWS
    assert len([r for r in registry if r.get("trace_observation_adapter")]) == 775
