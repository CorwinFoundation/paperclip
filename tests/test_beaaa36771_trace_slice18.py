from dataclasses import replace
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import AppendOnlyTrace, ControlledToolDispatcher, EffectStore, ToolSpec, _single_event
from tools.active_test_registry import build_registry
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


EXPECTED = {
    "agent_identity_signed_handoff": {f"bb18718.agent_identity_signed_handoff.{n:02d}" for n in range(1, 25)},
    "authorization_revocation_race": {f"bb18716.authorization_revocation_race.{n:02d}" for n in (3, 8, 10)},
    "idempotency_retry_double_execution": {f"bb18716.idempotency_retry_double_execution.{n:02d}" for n in (1,2,3,4,5,7,8,9,10,12,13,14,15,17,18,19,21,22,23,24)},
}


def test_exact_slice18_roster_and_total():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter")]
    assert len(rows) == 529
    for family, ids in EXPECTED.items():
        assert {r["case_id"] for r in rows if r["test_id"] == family} == ids


def test_all_slice18_rows_execute_without_target_or_judge(tmp_path):
    executor = RegisteredCaseExecutor(subject=MockRunner("target", lambda _: "unused"),
        judge=MockRunner("judge", lambda _: "unused"), evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=1))
    for row in build_registry():
        if row.get("case_id") not in EXPECTED.get(row.get("test_id"), set()):
            continue
        result = executor(row, SimpleNamespace(job_id="slice18", scan_id="slice18", agent_id="target", seed=0, capability_manifest=None))
        assert (result.status, result.verdict, result.target_calls, result.judge_calls) == ("complete", "pass", 0, 0)


def test_trace_shape_guard_rejects_missing_and_malformed_receipts():
    trace, effects = AppendOnlyTrace(), EffectStore(); d = ControlledToolDispatcher(trace=trace, effects=effects)
    d.register(ToolSpec("x", "s", lambda p: p)); d.dispatch("x", {}, scope="s", idempotency_key="k")
    event = trace.events()[0]
    assert _single_event((), "x") is None
    assert _single_event((replace(event, side_effect_receipt=None),), "x") is None
    assert _single_event((event,), "other") is None
