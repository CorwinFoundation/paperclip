from pathlib import Path

import yaml

from harness.bench.consequential_action_confirmation import (
    AUTHORIZED_CASE_IDS,
    run_report_only_case,
)
from tools.probe_registry import build_registry


CONSEQUENTIAL = {"cac-02", "cac-03", "cac-08"}
NEGATIVE_CONTROLS = {"cac-11", "cac-12"}


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


def test_authoritative_registry_exposes_bound_bounded_tranche() -> None:
    mapping_path = (
        Path(__file__).resolve().parents[1]
        / "rubric"
        / "probe_mappings"
        / "consequential_action_confirmation.yaml"
    )
    mapping = yaml.safe_load(
        mapping_path.read_text()
    )
    declared_report_only_ids = {
        probe["id"]
        for probe in mapping["probes"]
        if probe.get("report_only_execution", {}).get("reported") is True
    }
    assert declared_report_only_ids == set(AUTHORIZED_CASE_IDS)

    family_rows = [
        row for row in build_registry()["rows"]
        if row["corpus_or_family"] == "consequential_action_confirmation"
    ]
    legacy_production_rows = [
        row for row in family_rows
        if row["mode"] == "production_worker_registry_v0.1"
        and row["case_id_or_probe_id"] in AUTHORIZED_CASE_IDS
    ]
    assert len(legacy_production_rows) == len(AUTHORIZED_CASE_IDS)

    reported_rows = [row for row in family_rows if row["categories"]["reported"]]
    assert len(reported_rows) == len(AUTHORIZED_CASE_IDS)

    gated_rows = {
        row["case_id_or_probe_id"]: row
        for row in family_rows
        if row["case_id_or_probe_id"] in AUTHORIZED_CASE_IDS
    }
    assert set(gated_rows) == set(AUTHORIZED_CASE_IDS)
    for row in gated_rows.values():
        categories = row["categories"]
        assert categories["scoring_active"] is False
        assert categories["blocked_unsupported"] is True
        assert categories["executable_on_default_runtime"] is False
