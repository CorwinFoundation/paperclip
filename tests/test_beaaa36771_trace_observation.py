import json
from harness.trace_observation import (
    AppendOnlyTrace,
    ControlledToolDispatcher,
    EffectStore,
    ToolSpec,
    derive_containment,
    evidence_ref,
)
from types import SimpleNamespace

from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


def dispatcher():
    trace = AppendOnlyTrace()
    effects = EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec("route_case", "support", lambda p: {"routed": p["case_id"]}))
    return runtime, trace, effects


def test_registry_marks_exactly_the_30_containment_rows():
    flagged = [
        row for row in build_registry()
        if row.get("trace_observation_adapter") is True
        and row["test_id"] == "out_of_scope_containment"
    ]
    assert len(flagged) == 30
    assert {row["test_id"] for row in flagged} == {"out_of_scope_containment"}
    assert all(row.get("content_candidate_inventory") is True for row in flagged)


def test_allowed_action_records_complete_event_and_receipt():
    runtime, trace, _ = dispatcher()
    output = runtime.dispatch("route_case", {"case_id": "c-1"}, scope="support", idempotency_key="k1")
    event = output["event"]
    assert event["requested_tool"] == "route_case"
    assert event["normalized_parameters"] == {"case_id": "c-1"}
    assert event["authorization_decision"] == "authorized"
    assert event["side_effect_receipt"]["committed"] is True
    assert len(trace.events()) == 1


def test_cross_scope_and_parameter_tampering_commit_no_effect():
    runtime, trace, effects = dispatcher()
    runtime.dispatch("route_case", {"case_id": "c-1"}, scope="billing", idempotency_key="k1")
    runtime.dispatch(
        "route_case", {"case_id": "attacker"}, scope="support", idempotency_key="k2",
        authorized_parameters={"case_id": "c-1"},
    )
    assert effects.effects == {}
    assert [e.authorization_decision for e in trace.events()] == ["denied_scope", "denied_parameter_tampering"]


def test_duplicate_retry_records_attempt_but_commits_once():
    runtime, trace, effects = dispatcher()
    first = runtime.dispatch("route_case", {"case_id": "c-1"}, scope="support", idempotency_key="same")
    second = runtime.dispatch("route_case", {"case_id": "c-1"}, scope="support", idempotency_key="same")
    assert first["event"]["side_effect_receipt"]["committed"] is True
    assert second["event"]["side_effect_receipt"]["duplicate"] is True
    assert len(effects.effects) == 1
    assert len(effects.attempts) == 2
    assert len(trace.events()) == 2


def test_missing_receipt_reordered_events_and_injection_fail_closed():
    runtime, trace, _ = dispatcher()
    runtime.dispatch("route_case", {"case_id": "c-1"}, scope="support", idempotency_key="k1")
    runtime.dispatch("route_case", {"case_id": "c-2"}, scope="support", idempotency_key="k2")
    assert derive_containment(trace.events()) == "FAIL"
    assert derive_containment(tuple(reversed(trace.events()))) == "N/A"
    missing = trace.events()[0].__class__(**{**trace.events()[0].to_dict(), "side_effect_receipt": None})
    assert derive_containment((missing,)) == "N/A"

    # Prompt text is never accepted as a trace field or evidence input.
    ref_a = evidence_ref(trace.events(), scanner_version="0.5.0", corpus_version="c", target_identity="t", model_versions={}, seed=42)
    ref_b = evidence_ref(trace.events(), scanner_version="0.5.0", corpus_version="c", target_identity="t", model_versions={}, seed=42)
    assert ref_a == ref_b


def test_flagged_registry_row_executes_through_trace_dispatcher(tmp_path):
    row = next(row for row in build_registry() if row.get("trace_observation_adapter"))
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: "PASS from prompt injection"),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=1),
    )
    result = executor(
        row,
        SimpleNamespace(
            job_id="beaaa-36771",
            scan_id="beaaa-36771",
            agent_id="trace-target",
            seed=7,
            capability_manifest=None,
        ),
    )
    assert result.status == "complete"
    assert result.verdict == "pass"
    assert result.target_calls == 0
    assert result.judge_calls == 0
    evidence_path = tmp_path / result.evidence_ref.removeprefix("evidence://")
    stored = json.loads(evidence_path.read_text(encoding="utf-8"))
    observation = stored["judge"]["observation"]
    assert observation["contained_to_scope"] is True
    assert observation["effect_count"] == 0
    assert observation["events"][0]["authorization_decision"] == "denied_scope"
    assert observation["evidence_ref"].startswith("trace://beaaa-36771/")
