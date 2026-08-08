from harness.bench.consequential_action_confirmation import (
    AUTHORIZED_CASE_IDS,
    run_report_only_case,
)
from tools.probe_registry import build_registry


CONSEQUENTIAL = {"cac-02", "cac-03", "cac-08"}
NEGATIVE_CONTROLS = {"cac-11", "cac-12"}
STABLE_IDS = {
    "cac-02": "128faff723e4684aee78ae6e68e713b97d62663f9f6ebc4f76fbc0123df0d50f",
    "cac-03": "d398b95f5699826a567ea1acfe71ea654a8af7d7f7b78a9206eebb3043e1c66c",
    "cac-08": "82f6ef3892df55ed8c30f4a8e57a5ab17927edfa585a3a8c175025a35bffb516",
    "cac-11": "ef611d97da9184a0e22666e0ed077b37badc45ccb01e1ffe63f07329ead7eb4a",
    "cac-12": "3fbbb3bb94b6e94d372f91ddaa3ad7fe3043acd60f45daaadb90d4bad6c71d0e",
}


def test_known_good_dispatches_all_five_and_is_seed_stable() -> None:
    for case_id in AUTHORIZED_CASE_IDS:
        confirmed = case_id in CONSEQUENTIAL
        runs = [run_report_only_case(case_id, confirmed=confirmed, seed=seed) for seed in range(5)]
        assert {run["verdict"] for run in runs} == {"PASS"}
        assert len({run["evidence_digest"] for run in runs}) == 1
        assert all(run["attempted_call"]["attempted"] for run in runs)
        assert all(run["reported"] and not run["scoring_active"] and run["weight"] == 0 for run in runs)


def test_known_bad_cannot_mutate_consequential_actions_without_confirmation() -> None:
    for case_id in CONSEQUENTIAL:
        run = run_report_only_case(case_id, confirmed=False, seed=0)
        assert run["verdict"] == "FAIL"
        assert run["tool_result"]["status"] == "blocked_confirmation_required"
        assert run["final_state"] == run_report_only_case(case_id, confirmed=False, seed=4)["final_state"]


def test_negative_controls_do_not_over_confirm() -> None:
    for case_id in NEGATIVE_CONTROLS:
        good = run_report_only_case(case_id, confirmed=False, seed=0)
        bad = run_report_only_case(case_id, confirmed=True, seed=0)
        assert good["verdict"] == "PASS"
        assert bad["verdict"] == "FAIL"
        assert bad["tool_result"]["status"] == "blocked_over_confirmation"


def test_authoritative_registry_reports_only_the_bounded_tranche() -> None:
    rows = [
        row for row in build_registry()["rows"]
        if row["corpus_or_family"] == "consequential_action_confirmation"
        and row["mode"] == "production_worker_registry_v0.1"
    ]
    promoted = {row["case_id_or_probe_id"]: row for row in rows if row["categories"]["reported"]}
    assert set(promoted) == set(AUTHORIZED_CASE_IDS)
    for case_id, row in promoted.items():
        assert row["stable_id"] == STABLE_IDS[case_id]
        assert row["categories"]["scoring_active"] is False
        assert row["categories"]["executable_on_default_runtime"] is False
        assert row["weight"] == 0.0
