import json
from types import SimpleNamespace

from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget

SELECTED = {
    ("approval_freshness_replay", f"bb18716.approval_freshness_replay.{n:02d}")
    for n in range(1, 25) if n not in {9, 24}
} | {
    ("authorization_revocation_race", f"bb18716.authorization_revocation_race.{n:02d}")
    for n in (1, 2)
}

def test_exactly_24_slice_rows_are_bound():
    rows = [r for r in build_registry() if r.get("trace_observation_adapter") and (r["test_id"], r["case_id"]) in SELECTED]
    assert {(r["test_id"], r["case_id"]) for r in rows} == SELECTED

def test_all_24_rows_execute_n5_from_recorded_trace_without_model_calls(tmp_path):
    registry = build_registry()
    rows = [r for r in registry if r.get("trace_observation_adapter") and (r["test_id"], r["case_id"]) in SELECTED]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _: "unused"),
        judge=MockRunner("unused-judge", lambda _: "unused"),
        evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1),
    )
    for row in rows:
        for seed in range(5):
            result = executor(row, SimpleNamespace(job_id="beaaa-40887", scan_id="beaaa-40887", agent_id="trace-target", seed=seed, capability_manifest=None))
            assert result.status == "complete" and result.verdict == "pass"
            assert result.target_calls == result.judge_calls == 0
            evidence = json.loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]
            case = materialize_candidate_case(row["test_id"], row["case_id"])
            oracle = case.oracle
            assert evidence["reported_not_scored"] is True
            assert evidence["observation"][oracle["field"]] == oracle["expected"]
            assert evidence["observation"]["target_identity"] == "mock:trace-target"
            assert row["scoring_active"] is False
    assert len([r for r in registry if r.get("trace_observation_adapter")]) == 1181
