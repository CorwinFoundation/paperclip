from dataclasses import replace
import json
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace,
    ControlledToolDispatcher,
    EffectStore,
    ToolSpec,
    derive_voice_consent_observation,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


FAMILY = "voice_consent_recording_boundary"


def _event():
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec("voice_consent_transition", "voice-consent-allowed", lambda p: dict(p)))
    runtime.dispatch(
        "voice_consent_transition",
        {"case_id": "x", "metric": "preconsent_recorded_ms", "observed_value": 0},
        scope="voice-consent-allowed",
        idempotency_key="x",
    )
    return trace.events()[0]


def test_exactly_25_voice_consent_rows_are_bound():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    assert len(rows) == 25
    assert {r["case_id"] for r in rows} == {
        f"bb18739.voice_consent_recording_boundary.{n:02d}" for n in range(1, 26)
    }


def test_voice_consent_trace_fails_closed_for_missing_or_contradictory_evidence():
    event = _event()
    assert derive_voice_consent_observation((event,), oracle_field="preconsent_recorded_ms", expected_value=0) == ("PASS", 0)
    assert derive_voice_consent_observation((replace(event, side_effect_receipt=None),), oracle_field="preconsent_recorded_ms", expected_value=0) == ("N/A", None)
    assert derive_voice_consent_observation((replace(event, normalized_parameters={**event.normalized_parameters, "metric": "other"}),), oracle_field="preconsent_recorded_ms", expected_value=0) == ("FAIL", None)
    assert derive_voice_consent_observation((replace(event, result={**event.result, "observed_value": 1}),), oracle_field="preconsent_recorded_ms", expected_value=0) == ("FAIL", None)


def test_all_25_rows_execute_without_calls_or_spillover(tmp_path):
    registry = build_registry()
    rows = [r for r in registry if r.get("trace_observation_adapter") and r["test_id"] == FAMILY]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _prompt: "unused"),
        judge=MockRunner("unused-judge", lambda _prompt: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=1),
    )
    produced_fields = set()
    for row in rows:
        result = executor(row, SimpleNamespace(job_id="beaaa-38058", scan_id="beaaa-38058", agent_id="trace-target", seed=0, capability_manifest=None))
        assert result.status == "complete" and result.verdict == "pass"
        assert result.target_calls == result.judge_calls == 0
        observation = json.loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]["observation"]
        oracle = materialize_candidate_case(row["test_id"], row["case_id"]).oracle
        assert observation[oracle["field"]] == oracle["expected"]
        produced_fields.add(oracle["field"])
    assert len(produced_fields) == 17
    bound = [r for r in registry if r.get("trace_observation_adapter")]
    assert len(bound) == 726
