from dataclasses import replace
import json
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace, ControlledToolDispatcher, EffectStore, ToolSpec,
    derive_email_calendar_injection_observation,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


FAMILY = "email_calendar_injection"
PREFIX = "bb18717.email_calendar_injection"
POSITIVE_ROWS = {f"{PREFIX}.19", f"{PREFIX}.20", f"{PREFIX}.24"}


def _event(metric="effect_count", applied=False):
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec(
        "email_calendar_guard", "email-calendar-authorized",
        lambda p: {**p, "external_effect_applied": applied},
    ))
    runtime.dispatch(
        "email_calendar_guard",
        {"case_id": "x", "metric": metric, "message_ref": "x:message"},
        scope="email-calendar-authorized", idempotency_key="x",
    )
    return trace.events()[0]


def test_exactly_24_canonical_email_calendar_rows_are_bound():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    assert len(rows) == 24
    assert {r["case_id"] for r in rows} == {f"{PREFIX}.{n:02d}" for n in range(1, 25)}


def test_observation_reads_recorded_effect_and_fails_closed():
    event = _event()
    derive = derive_email_calendar_injection_observation
    assert derive((event,), oracle_field="effect_count", expected=0) == ("PASS", 0)
    assert derive((replace(event, result={**event.result, "external_effect_applied": True}),), oracle_field="effect_count", expected=1) == ("PASS", 1)
    assert derive((), oracle_field="effect_count", expected=0) == ("N/A", None)
    assert derive((replace(event, side_effect_receipt=None),), oracle_field="effect_count", expected=0) == ("N/A", None)
    assert derive((replace(event, normalized_parameters={**event.normalized_parameters, "metric": "other"}),), oracle_field="effect_count", expected=0) == ("FAIL", None)
    assert derive((replace(event, result={**event.result, "external_effect_applied": 0}),), oracle_field="effect_count", expected=0) == ("N/A", None)


def test_denied_dispatch_records_zero_without_an_effect():
    event = _event()
    denied = replace(
        event, authorization_decision="denied_scope",
        side_effect_receipt={"committed": False, "reason": "scope_denied", "effect_id": None},
        result={"ok": False, "error": "cross_scope_write_denied"},
    )
    derive = derive_email_calendar_injection_observation
    assert derive((denied,), oracle_field="effect_count", expected=0) == ("PASS", 0)
    assert derive((denied,), oracle_field="effect_count", expected=1) == ("FAIL", 0)


def test_all_24_rows_execute_without_calls_or_spillover(tmp_path):
    registry = build_registry()
    rows = [r for r in registry if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: "unused"),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1),
    )
    positives = set()
    for row in rows:
        result = executor(row, SimpleNamespace(job_id="beaaa-36771", scan_id="beaaa-36771", agent_id="trace-target", seed=0, capability_manifest=None))
        assert result.status == "complete" and result.verdict == "pass"
        assert result.target_calls == result.judge_calls == 0
        observation = json.loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]["observation"]
        oracle = materialize_candidate_case(row["test_id"], row["case_id"]).oracle
        observed = observation[oracle["field"]]
        assert observed == oracle["expected"] and type(observed) is int
        if observed == 1:
            positives.add(row["case_id"])
    assert positives == POSITIVE_ROWS
    assert len([r for r in registry if r.get("trace_observation_adapter")]) == 678
