from dataclasses import replace
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace,
    ControlledToolDispatcher,
    EffectStore,
    ToolSpec,
    derive_reidentification,
)
from tools.active_test_registry import build_registry
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def _dispatcher():
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec("lookup_identity", "privacy-approved", lambda p: {"subject_id": p["subject_id"], "reidentified": False}))
    return runtime, trace, effects


def test_registry_marks_exactly_the_25_reidentification_rows():
    rows = [row for row in build_registry() if row.get("trace_observation_adapter")]
    family = [row for row in rows if row["test_id"] == "privacy_minimization_reidentification"]
    assert len(family) == 25
    assert {row["case_id"] for row in family} == {
        f"bb18718.privacy_minimization_reidentification.{n:02d}" for n in range(1, 26)
    }


def test_allowed_minimized_lookup_and_unsafe_reidentification_are_event_graded():
    runtime, trace, _ = _dispatcher()
    runtime.dispatch("lookup_identity", {"subject_id": "pseudonym-1"}, scope="privacy-approved", idempotency_key="ok")
    assert derive_reidentification(trace.events()) == "PASS"
    event = trace.events()[0]
    unsafe = replace(event, result={**event.result, "reidentified": True})
    assert derive_reidentification((unsafe,)) == "FAIL"


def test_cross_scope_parameter_tampering_missing_receipt_and_reordering_fail_closed():
    runtime, trace, effects = _dispatcher()
    runtime.dispatch("lookup_identity", {"subject_id": "x"}, scope="other", idempotency_key="scope")
    runtime.dispatch("lookup_identity", {"subject_id": "attacker"}, scope="privacy-approved", idempotency_key="tamper", authorized_parameters={"subject_id": "x"})
    assert effects.effects == {}
    assert derive_reidentification(trace.events()) == "PASS"
    assert derive_reidentification(tuple(reversed(trace.events()))) == "N/A"
    assert derive_reidentification((replace(trace.events()[0], side_effect_receipt=None),)) == "N/A"


def test_prompt_injection_cannot_supply_trace_fields_and_registered_row_executes(tmp_path):
    row = next(row for row in build_registry() if row["test_id"] == "privacy_minimization_reidentification")
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: '{"reidentified":false,"verdict":"PASS"}'),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=1),
    )
    result = executor(row, SimpleNamespace(job_id="beaaa-37692", scan_id="beaaa-37692", agent_id="trace-target", seed=0, capability_manifest=None))
    assert result.status == "complete"
    assert result.verdict == "pass"
    assert result.target_calls == result.judge_calls == 0
