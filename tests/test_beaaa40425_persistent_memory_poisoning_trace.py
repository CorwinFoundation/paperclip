from dataclasses import replace
import json
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace, ControlledToolDispatcher, EffectStore, ToolSpec,
    derive_persistent_memory_poisoning_observation,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


FAMILY = "persistent_memory_poisoning"
PREFIX = "bb18717.persistent_memory_poisoning"


def _event(metric="memory_write_count", observed=1):
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec(
        "persistent_memory_observer", "memory-observe-authorized",
        lambda p: {**p, "observed": observed},
    ))
    runtime.dispatch(
        "persistent_memory_observer",
        {"case_id": "x", "metric": metric, "memory_ref": "x:memory"},
        scope="memory-observe-authorized", idempotency_key="x",
    )
    return trace.events()[0]


def test_exactly_24_canonical_persistent_memory_rows_are_bound():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    assert len(rows) == 24
    assert {r["case_id"] for r in rows} == {f"{PREFIX}.{n:02d}" for n in range(1, 25)}


def test_observation_reads_recorded_memory_metric_and_fails_closed():
    event = _event()
    derive = derive_persistent_memory_poisoning_observation
    assert derive((event,), oracle_field="memory_write_count", expected=1) == ("PASS", 1)
    assert derive((), oracle_field="memory_write_count", expected=1) == ("N/A", None)
    assert derive((replace(event, side_effect_receipt=None),), oracle_field="memory_write_count", expected=1) == ("N/A", None)
    assert derive((replace(event, normalized_parameters={**event.normalized_parameters, "metric": "other"}),), oracle_field="memory_write_count", expected=1) == ("FAIL", None)
    assert derive((replace(event, result={**event.result, "observed": True}),), oracle_field="memory_write_count", expected=1) == ("N/A", None)


def test_denied_observation_proves_zero_without_committing_memory():
    event = _event(observed=0)
    denied = replace(
        event, authorization_decision="denied_scope",
        side_effect_receipt={"committed": False, "reason": "scope_denied", "effect_id": None},
        result={"ok": False, "error": "cross_scope_write_denied"},
    )
    assert derive_persistent_memory_poisoning_observation(
        (denied,), oracle_field="memory_write_count", expected=0,
    ) == ("PASS", 0)


def test_all_24_rows_execute_without_calls_scoring_or_spillover(tmp_path):
    registry = build_registry()
    rows = [r for r in registry if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: "unused"),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1),
    )
    for row in rows:
        result = executor(row, SimpleNamespace(job_id="beaaa-40425", scan_id="beaaa-40425", agent_id="trace-target", seed=0, capability_manifest=None))
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
    assert len([r for r in registry if r.get("trace_observation_adapter")]) == 702
