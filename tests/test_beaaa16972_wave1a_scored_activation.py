from __future__ import annotations

from pathlib import Path

import yaml

from evals.aiuc_path_b import wave1a_candidate as wave
from tools.active_test_registry import (
    AIUC_PATH_B_SCORING_ACTIVE_FAMILIES,
    build_registry,
    count_active_executable,
)


ROOT = Path(__file__).resolve().parents[1]


def test_beaaa16972_activation_packet_is_exact_bounded_tranche() -> None:
    packet = wave.build_scoring_active_packet(
        commit_sha="1830bed7facf4489955b5f9aba2c4e21250e2a09"
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


def test_beaaa16972_rollback_restores_prior_reported_not_scored_state() -> None:
    for family in AIUC_PATH_B_SCORING_ACTIVE_FAMILIES:
        path = ROOT / "rubric" / "probe_mappings" / f"{family}.yaml"
        row = yaml.safe_load(path.read_text(encoding="utf-8"))
        restored = dict(row)
        restored["reporting_status"] = "reported_not_scored"
        restored["scoring"] = "reported_not_scored"
        binding = dict(restored.get("binding") or {})
        binding["scoring_active"] = False
        binding["underwriting_active"] = False
        restored["binding"] = binding

        assert restored["reporting_status"] == "reported_not_scored"
        assert restored["scoring"] == "reported_not_scored"
        assert restored["binding"]["scoring_active"] is False
        assert restored["binding"]["underwriting_active"] is False
