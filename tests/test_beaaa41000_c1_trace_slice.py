import json
from types import SimpleNamespace

from harness.runner import MockRunner
from tools.active_test_registry import build_registry
from tools.content_candidate_inventory import materialize_candidate_case
from tools.registered_case_executor import EvidenceStore, RegisteredCaseExecutor, ScanBudget


SELECTED = {
    ("recipient_identity_integrity", f"bb18690.recipient_identity_integrity.{n:03d}")
    for n in range(12, 14)
} | {
    ("transaction_end_state", f"bb18690.transaction_end_state.{n:03d}")
    for n in range(1, 14)
} | {
    ("rag_grounding_retrieval_integrity", f"bb18690.rag_grounding_retrieval_integrity.{n:03d}")
    for n in range(1, 10)
}


def test_exactly_24_c1_rows_are_newly_bound():
    rows = [row for row in build_registry() if row.get("trace_observation_adapter")]
    assert {(row["test_id"], row["case_id"]) for row in rows if (row["test_id"], row["case_id"]) in SELECTED} == SELECTED
    assert len(rows) == 1158


def test_all_24_rows_execute_n5_from_recorded_trace_without_model_calls(tmp_path):
    rows = [row for row in build_registry() if (row["test_id"], row["case_id"]) in SELECTED]
    executor = RegisteredCaseExecutor(
        subject=MockRunner("trace-target", lambda _: "unused"),
        judge=MockRunner("unused-judge", lambda _: "unused"),
        evidence_store=EvidenceStore(tmp_path), budget=ScanBudget(max_target_calls=1),
    )
    for row in rows:
        for seed in range(5):
            seeded_row = {**row, "scan_seed": seed}
            result = executor(seeded_row, SimpleNamespace(job_id="beaaa-41000", scan_id="beaaa-41000", agent_id="trace-target", seed=seed, capability_manifest=None))
            assert result.status == "complete" and result.verdict == "pass"
            assert result.target_calls == result.judge_calls == 0
            evidence = json.loads((tmp_path / result.evidence_ref.removeprefix("evidence://")).read_text())["judge"]
            for assertion in materialize_candidate_case(row["test_id"], row["case_id"]).oracle["assertions"]:
                assert evidence["observation"][assertion["field"]] == assertion["equals"]
            assert evidence["observation"]["target_identity"] == "mock:trace-target"
            assert row["scoring_active"] is False
