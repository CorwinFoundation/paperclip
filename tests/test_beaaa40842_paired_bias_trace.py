import json
from types import SimpleNamespace

from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


FAMILIES = {
    "p2_5_sentiment_skew_by_attribute": "P2.5-BIAS",
    "p2_6_mitigation_evidence": "P2.6-BIAS",
}


def test_exactly_24_sentiment_and_mitigation_rows_are_bound():
    rows = [
        row
        for row in build_registry()
        if row.get("trace_observation_adapter") and row["test_id"] in FAMILIES
    ]
    assert len(rows) == 24
    assert {row["case_id"] for row in rows} == {
        f"{prefix}-{number:02d}"
        for prefix in FAMILIES.values()
        for number in range(1, 13)
    }


def test_all_24_rows_execute_from_receipt_backed_trace_without_model_calls(tmp_path):
    registry = build_registry()
    rows = [
        row
        for row in registry
        if row.get("trace_observation_adapter") and row["test_id"] in FAMILIES
    ]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _: "unused"),
        judge=MockRunner("unused-judge", lambda _: "unused"),
        evidence_store=EvidenceStore(tmp_path),
        budget=ScanBudget(max_target_calls=1),
    )
    for row in rows:
        for seed in range(5):
            result = executor(
                row,
                SimpleNamespace(
                    job_id="beaaa-40842",
                    scan_id="beaaa-40842",
                    agent_id="trace-target",
                    seed=seed,
                    capability_manifest=None,
                ),
            )
            assert result.status == "complete" and result.verdict == "pass"
            assert result.target_calls == result.judge_calls == 0
            evidence = json.loads(
                (tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text()
            )["judge"]
            assert evidence["reported_not_scored"] is True
            observation = evidence["observation"]
            expected = materialize_candidate_case(
                row["test_id"], row["case_id"]
            ).expected["expected_observable_behavior"]
            assert observation["observed_label"] == expected["expected_label"]
            assert observation["target_identity"] == "mock:trace-target"
            assert row["scoring_active"] is False
    assert len([r for r in registry if r.get("trace_observation_adapter")]) == 1014
