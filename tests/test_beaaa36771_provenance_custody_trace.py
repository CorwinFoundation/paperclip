from dataclasses import replace
import json
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace, ControlledToolDispatcher, EffectStore, ToolSpec,
    derive_provenance_custody_observation,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


FAMILY = "provenance_chain_of_custody"
PREFIX = "bb18740.provenance_chain_of_custody"
BOOLEAN_ROWS = {
    f"{PREFIX}.15": "timestamp_lineage_ok",
    f"{PREFIX}.18": "historical_signature_valid",
}


def _event(metric="verified_derivative_count"):
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec(
        "provenance_custody_ledger", "provenance-custody-authorized",
        lambda p: {**p, "lineage_valid": True, "custody_violation": False},
    ))
    runtime.dispatch(
        "provenance_custody_ledger",
        {
            "case_id": "x", "metric": metric, "artifact_ref": "x:artifact",
            "custody_finding": metric in {"identity_collision_count", "ledger_fork_count"},
        },
        scope="provenance-custody-authorized", idempotency_key="x",
    )
    return trace.events()[0], len(effects.effects)


def test_exactly_25_provenance_custody_rows_are_bound():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    assert len(rows) == 25
    assert {r["case_id"] for r in rows} == {f"{PREFIX}.{n:02d}" for n in range(1, 26)}


def test_custody_observation_fails_closed():
    event, committed = _event()
    d = derive_provenance_custody_observation
    assert d((event,), oracle_field="verified_derivative_count", expected=1, committed_effects=committed) == ("PASS", 1)
    assert d((), oracle_field="verified_derivative_count", expected=1, committed_effects=committed) == ("N/A", None)
    assert d((replace(event, side_effect_receipt=None),), oracle_field="verified_derivative_count", expected=1, committed_effects=committed) == ("N/A", None)
    assert d((replace(event, normalized_parameters={**event.normalized_parameters, "metric": "other"}),), oracle_field="verified_derivative_count", expected=1, committed_effects=committed) == ("FAIL", None)


def test_boolean_lineage_is_not_coerced_to_an_integer():
    event, committed = _event("timestamp_lineage_ok")
    d = derive_provenance_custody_observation
    assert d((event,), oracle_field="timestamp_lineage_ok", expected=True, committed_effects=committed) == ("PASS", True)
    assert d((replace(event, result={**event.result, "lineage_valid": 1}),), oracle_field="timestamp_lineage_ok", expected=True, committed_effects=committed) == ("N/A", None)


def test_rejected_transfer_records_only_explicit_custody_defects():
    event, _ = _event("identity_collision_count")
    denied = replace(
        event, authorization_decision="denied_scope",
        side_effect_receipt={"committed": False, "reason": "scope_denied", "effect_id": None},
        result={"ok": False, "error": "cross_scope_write_denied"},
    )
    d = derive_provenance_custody_observation
    assert d((denied,), oracle_field="identity_collision_count", expected=1, committed_effects=0) == ("PASS", 1)
    denied_zero = replace(denied, normalized_parameters={
        **denied.normalized_parameters, "metric": "orphan_derivative_count",
        "custody_finding": False,
    })
    assert d((denied_zero,), oracle_field="orphan_derivative_count", expected=0, committed_effects=0) == ("PASS", 0)


def test_all_25_rows_execute_without_calls_or_spillover(tmp_path):
    registry = build_registry()
    rows = [r for r in registry if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: "unused"),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1),
    )
    seen_boolean = set()
    for row in rows:
        result = executor(row, SimpleNamespace(job_id="beaaa-36771", scan_id="beaaa-36771", agent_id="trace-target", seed=0, capability_manifest=None))
        assert result.status == "complete" and result.verdict == "pass"
        assert result.target_calls == result.judge_calls == 0
        observation = json.loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]["observation"]
        oracle = materialize_candidate_case(row["test_id"], row["case_id"]).oracle
        observed = observation[oracle["field"]]
        assert observed == oracle["expected"]
        assert type(observed) is type(oracle["expected"])
        if row["case_id"] in BOOLEAN_ROWS:
            assert oracle["field"] == BOOLEAN_ROWS[row["case_id"]]
            seen_boolean.add(row["case_id"])
    assert seen_boolean == set(BOOLEAN_ROWS)
    assert len([r for r in registry if r.get("trace_observation_adapter")]) == 558
