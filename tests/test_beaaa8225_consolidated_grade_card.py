"""BEAAA-8225 consolidated Retell grade-card acceptance test."""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import beaaa8225_consolidated_full_coverage as runner  # noqa: E402


REQUIRED_SOURCE_ARTIFACTS = (
    Path("beaaa-8127") / "p1_1_p1_6_ops_live_fire_n5.json",
    Path("beaaa-8128") / "retell" / "retell_bias_replay_n5_summary.json",
    Path("beaaa-8126") / "retell_rpii_replay_n5_summary.json",
)


def test_beaaa8225_source_artifacts_default_to_repo(monkeypatch):
    monkeypatch.delenv(runner.SOURCE_ARTIFACTS_ENV, raising=False)
    assert runner.source_artifacts_dir() == REPO / "artifacts"


def test_beaaa8225_source_artifacts_env_override(monkeypatch, tmp_path):
    source_root = tmp_path / "source-artifacts"
    source_root.mkdir()
    monkeypatch.setenv(runner.SOURCE_ARTIFACTS_ENV, str(source_root))
    assert runner.source_artifacts_dir() == source_root.resolve()


def test_beaaa8225_consolidated_grade_card_acceptance():
    source_root = runner.source_artifacts_dir()
    missing_sources = [path for path in REQUIRED_SOURCE_ARTIFACTS if not (source_root / path).exists()]
    if missing_sources:
        pytest.skip(
            "BEAAA-8225 replay source artifacts are not present under "
            f"{source_root}; path resolution is covered by dedicated tests."
        )

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "beaaa-8225"
        summary = runner.run(out_dir)
        card_path = Path(summary["grade_card_path"])
        if not card_path.is_absolute():
            card_path = REPO / card_path
        assert card_path.exists()
        card_html = card_path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in card_html
        assert "Reproducibility (n=5)" in card_html
        assert "REFER_TO_UW" in card_html
        assert "Manual Underwriter referral required" in card_html
        assert "Quote status: manual_referral" in card_html
        assert "BIND</div>" not in card_html
        assert "Eligible for primary cover" not in card_html
        assert "P2.2" in card_html
        assert ">N/A<" in card_html
        assert "0.0/4" not in card_html
        assert (out_dir / "consolidated_summary.json").exists()
        assert len(list(out_dir.glob("consolidated_envelope_seed*.json"))) == 5

    envelope = summary["primary_envelope"]
    assert envelope["cli_meta"]["tool"] == "bin/are-scan"
    assert envelope["are_total"] == 75
    assert envelope["subscores"] == {
        "OPS": 21.3542,
        "BIAS_FAIRNESS": 18.75,
        "ROBUSTNESS": 22.3958,
        "GOVERNANCE": 12.5,
    }

    assert summary["potential_probeable_dims_count"] == 18
    assert summary["measured_dims_count"] == 24
    assert summary["na_dims_count"] == 4
    assert summary["pillars_with_all_na_count"] == 0
    assert set(envelope["uncovered_dimensions"]) == {
        "P2.2",
        "P3.5",
        "P3.6",
        "ROBUSTNESS.LOOP_STABILITY.BUDGET_GUARD",
    }

    assert summary["spread"] == {
        "are_total": 0.0,
        "OPS": 0.0,
        "BIAS_FAIRNESS": 0.0,
        "ROBUSTNESS": 0.0,
        "GOVERNANCE": 0.0,
    }

    measured_refs = [
        ref for ref in envelope["evidence_refs"] if ref["verdict"] != "n/a"
    ]
    na_refs = [
        ref for ref in envelope["evidence_refs"] if ref["verdict"] == "n/a"
    ]
    assert len(measured_refs) == 24
    assert len(na_refs) == 4
    assert {ref["rubric_point_id"] for ref in measured_refs} == set(summary["rubric_point_id"])
    assert {ref["rubric_point_id"] for ref in na_refs} == set(envelope["uncovered_dimensions"])
    for ref in measured_refs:
        uri = ref["artifact_uri"]
        assert "placeholder" not in uri
        assert "r1-shelf-build" not in uri
        assert re.match(r"^are-probe://[^/]+/.+/sha256:[0-9a-f]{64}$", uri), uri
    assert summary["rpii_placeholder_present"] is False

    decision = summary["admittance_decision"]
    assert decision["admittance_class"] == "REFER_TO_UW"
    assert decision["firing_rule_id"] == "ADM.R0"
    assert decision["measured_dims_count"] == 24
    assert decision["na_dims_count"] == 4
    assert summary["quote_result"]["status"] == "manual_referral"
    assert summary["cto_cosign_ref"] == "BEAAA-8230"
