from __future__ import annotations

from pathlib import Path
from shutil import copytree

import pytest
import yaml

from evals.aiuc_path_b import wave1a_candidate as wave
from tools.active_test_registry import (
    AIUC_PATH_B_SCORING_ACTIVE_FAMILIES,
    build_registry,
    count_active_executable,
)


ROOT = Path(__file__).resolve().parents[1]
MAPPING_DIR = ROOT / "rubric" / "probe_mappings"


@pytest.mark.skip(reason="Requires a fresh out-of-band production-authority signature.")
def test_beaaa16972_activation_packet_is_exact_bounded_tranche() -> None:
    commit_sha = "1830bed7facf4489955b5f9aba2c4e21250e2a09"
    packet = wave.build_scoring_active_packet(
        commit_sha=commit_sha,
        target=wave.reference_authorization(),
    )

    assert packet["fail_closed"] is False
    assert packet["ticket_ref"] == "BEAAA-16972"
    assert packet["source_candidate_ref"] == "BEAAA-16945"
    assert packet["underwriting_cosign_ref"] == "BEAAA-16971"
    assert packet["promotion_state"] == "scoring_active"
    assert packet["underwriting_state"] == "underwriting_active"
    assert packet["counts"]["unique_scenario_count"] == 35
    assert packet["counts"]["execution_count"] == 175
    assert packet["counts"]["underwriting_active_count"] == 35
    assert packet["score_impact"] == {
        "are_total_delta": 0,
        "pillar_deltas": {"GOVERNANCE": 0, "ROBUSTNESS": 0, "BIAS_FAIRNESS": 0, "OPS": 0},
        "band_delta": 0,
        "pricing_delta": 0,
    }
    assert packet["false_positive_budget"]["known_false_positives_at_activation"] == 0
    assert set(packet["peril_mapping"]) == AIUC_PATH_B_SCORING_ACTIVE_FAMILIES
    assert packet["signed_manifest"]["signature"].startswith("sha256:")


def test_beaaa16972_registry_promotes_only_two_families() -> None:
    rows = build_registry()
    promoted = [row for row in rows if row["test_id"] in AIUC_PATH_B_SCORING_ACTIVE_FAMILIES]

    assert len(promoted) == 35
    assert count_active_executable(promoted) == 35
    assert {row["reporting_status"] for row in promoted} == {"scoring_active"}
    assert {row["scoring_active"] for row in promoted} == {True}
    assert {row["activation_blocker"] for row in promoted} == {None}
    for family in AIUC_PATH_B_SCORING_ACTIVE_FAMILIES:
        mapping = yaml.safe_load((MAPPING_DIR / f"{family}.yaml").read_text(encoding="utf-8"))
        assert mapping["scoring"] == "reported_not_scored"


def test_beaaa16972_executable_rollback_restores_181_without_touching_unrelated_rows(tmp_path) -> None:
    mapping_dir = tmp_path / "probe_mappings"
    copytree(MAPPING_DIR, mapping_dir)

    before_rows = build_registry(mapping_dir)
    assert count_active_executable(before_rows) == 216

    unrelated_before = {
        (row["test_id"], row["case_id"], row["risk_or_rubric_id"]): row
        for row in before_rows
        if row["test_id"] not in AIUC_PATH_B_SCORING_ACTIVE_FAMILIES
        and row["status"] == "active"
        and row["production_runner_executable"] is True
    }

    for family in AIUC_PATH_B_SCORING_ACTIVE_FAMILIES:
        path = mapping_dir / f"{family}.yaml"
        row = yaml.safe_load(path.read_text(encoding="utf-8"))
        row["reporting_status"] = "reported_not_scored"
        row["scoring"] = "reported_not_scored"
        binding = dict(row.get("binding") or {})
        binding["scoring_active"] = False
        binding["underwriting_active"] = False
        row["binding"] = binding
        path.write_text(yaml.safe_dump(row, sort_keys=False), encoding="utf-8")

    after_rows = build_registry(mapping_dir)
    assert count_active_executable(after_rows) == 181

    for family in AIUC_PATH_B_SCORING_ACTIVE_FAMILIES:
        restored_rows = [row for row in after_rows if row["test_id"] == family]
        assert {row["reporting_status"] for row in restored_rows} == {"reported_not_scored"}
        assert {row["scoring_active"] for row in restored_rows} == {False}
        assert count_active_executable(restored_rows) == 0
        mapping = yaml.safe_load((mapping_dir / f"{family}.yaml").read_text(encoding="utf-8"))
        assert mapping["binding"]["underwriting_active"] is False

    unrelated_after = {
        (row["test_id"], row["case_id"], row["risk_or_rubric_id"]): row
        for row in after_rows
        if row["test_id"] not in AIUC_PATH_B_SCORING_ACTIVE_FAMILIES
        and row["status"] == "active"
        and row["production_runner_executable"] is True
    }
    assert unrelated_after == unrelated_before
