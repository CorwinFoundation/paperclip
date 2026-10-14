from dataclasses import replace
import json
from types import SimpleNamespace

from harness.runner import MockRunner
from harness.trace_observation import (
    AppendOnlyTrace, ControlledToolDispatcher, EffectStore, ToolSpec,
    derive_paired_bias_observation,
)
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget

FAMILIES = {
    "p2_1_demographic_parity_gap": "P2.1-BIAS",
    "p2_2_equal_opportunity_gap": "P2.2-BIAS",
}


def _event(observed_label="parity_match"):
    trace, effects = AppendOnlyTrace(), EffectStore()
    runtime = ControlledToolDispatcher(trace=trace, effects=effects)
    runtime.register(ToolSpec("paired_bias_observer", "paired-comparison-inspect", lambda p: {**p, "observed_label": observed_label}))
    runtime.dispatch("paired_bias_observer", {"case_id": "x", "axis": "age", "context": "lending", "domain_tag": "decision", "comparison_ref": "target:x"}, scope="paired-comparison-inspect", idempotency_key="x")
    return trace.events()[0]


def test_exactly_24_accepted_paired_bias_rows_are_bound():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and r["test_id"] in FAMILIES]
    assert len(rows) == 24
    assert {r["case_id"] for r in rows} == {
        f"{prefix}-{n:02d}" for prefix in FAMILIES.values() for n in range(1, 13)
    }


def test_observation_reads_receipt_backed_comparison_and_fails_closed():
    event = _event()
    derive = derive_paired_bias_observation
    kwargs = dict(expected_label="parity_match", axis="age", context="lending", domain_tag="decision")
    assert derive((event,), **kwargs) == ("PASS", "parity_match")
    assert derive((replace(event, result={**event.result, "observed_label": "severe_gap"}),), **kwargs) == ("FAIL", "severe_gap")
    assert derive((), **kwargs) == ("N/A", None)
    assert derive((replace(event, side_effect_receipt=None),), **kwargs) == ("N/A", None)
    assert derive((replace(event, normalized_parameters={**event.normalized_parameters, "axis": "other"}),), **kwargs) == ("FAIL", None)


def test_all_24_rows_execute_without_target_or_judge_calls(tmp_path):
    registry = build_registry()
    rows = [r for r in registry if r.get("trace_observation_adapter") and r["test_id"] in FAMILIES]
    executor = RegisteredCaseExecutor(subject=MockRunner("trace-target", lambda _: "unused"), judge=MockRunner("unused-judge", lambda _: "unused"), evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1))
    for row in rows:
        for seed in range(5):
            result = executor(row, SimpleNamespace(job_id="beaaa-40798", scan_id="beaaa-40798", agent_id="trace-target", seed=seed, capability_manifest=None))
            assert result.status == "complete" and result.verdict == "pass"
            assert result.target_calls == result.judge_calls == 0
            judge = json.loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]
            assert judge["reported_not_scored"] is True
            observation = judge["observation"]
            expected = materialize_candidate_case(row["test_id"], row["case_id"]).expected["expected_observable_behavior"]
            assert observation["observed_label"] == expected["expected_label"]
            assert row["scoring_active"] is False
    assert len([r for r in registry if r.get("trace_observation_adapter")]) == 1014
