"""BEAAA-8389 consolidated Rasa grade-card acceptance test."""
from __future__ import annotations

import re
import sys
import tempfile
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import beaaa8389_rasa_consolidated_grade_card as runner  # noqa: E402


def test_beaaa8389_rasa_consolidated_grade_card_acceptance():
    active_rubric_version = runner.Rubric.load(runner.RUBRIC_PATH).version
    assert runner.RUBRIC_VERSION == active_rubric_version

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "beaaa-8389"
        summary = runner.run(out_dir)
        card_path = Path(summary["grade_card_path"])
        if not card_path.is_absolute():
            card_path = REPO / card_path
        assert card_path.exists()
        card_html = card_path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in card_html
        assert "BackBond" in card_html
        assert "ARE Insurance" not in card_html
        assert "Agent Risk Exposure Report Card" not in card_html
        assert "Reproducibility (n=5)" in card_html
        assert "NO BIND" in card_html
        assert "ADM.R0" in card_html
        assert "Bind-eligibility failed" in card_html
        assert "AUTO_BIND" not in card_html
        assert "CONSTRAINED_BIND" not in card_html
        assert "Eligible for primary cover" not in card_html
        assert ">N/A<" in card_html
        assert "warn-badge" in card_html
        assert (out_dir / "consolidated_summary.json").exists()
        assert len(list(out_dir.glob("consolidated_envelope_seed*.json"))) == 5
        persisted_summary = json.loads((out_dir / "consolidated_summary.json").read_text(encoding="utf-8"))
        closeout_md = (out_dir / "consolidated_grade_card_closeout.md").read_text(encoding="utf-8")
        seed0 = json.loads((out_dir / "consolidated_envelope_seed0.json").read_text(encoding="utf-8"))

    envelope = summary["primary_envelope"]
    assert envelope["rubric_version"] == active_rubric_version
    assert seed0["rubric_version"] == active_rubric_version
    assert envelope["cli_meta"]["tool"] == "bin/are-scan"
    assert envelope["are_total"] == 1
    assert envelope["subscores"] == {
        "OPS": 0.0,
        "BIAS_FAIRNESS": 0.0,
        "ROBUSTNESS": 0.8333,
        "GOVERNANCE": 0.0,
    }
    assert envelope["model_versions"] == {
        "primary": runner.MODEL_PRIMARY,
        "judges": runner.MODEL_JUDGES,
    }
    assert envelope["seed"] == 0

    assert summary["contract_version"] == runner.CONTRACT_VERSION
    assert summary["scanner_version"] == runner.SCANNER_VERSION
    assert summary["corpus_version"] == runner.CORPUS_VERSION
    assert summary["probe_set_version"] == runner.PROBE_SET_VERSION
    assert summary["rubric_version"] == active_rubric_version
    assert persisted_summary["rubric_version"] == active_rubric_version
    assert f"rubric_version: `{active_rubric_version}`" in closeout_md
    assert "rubric_version: `1.4.0`" not in closeout_md
    assert summary["rates_version"] == runner.RATES_VERSION
    assert summary["measured_dims_count"] == 8
    assert summary["na_dims_count"] == 17
    assert summary["pillars_with_all_na_count"] == 1
    assert summary["spread"] == {
        "are_total": 0.0,
        "OPS": 0.0,
        "BIAS_FAIRNESS": 0.0,
        "ROBUSTNESS": 0.0,
        "GOVERNANCE": 0.0,
    }
    assert summary["n5_rows"] == [
        {"seed": 0, "are_total": 1, "OPS": 0.0, "BIAS_FAIRNESS": 0.0, "ROBUSTNESS": 0.8333, "GOVERNANCE": 0.0},
        {"seed": 1, "are_total": 1, "OPS": 0.0, "BIAS_FAIRNESS": 0.0, "ROBUSTNESS": 0.8333, "GOVERNANCE": 0.0},
        {"seed": 2, "are_total": 1, "OPS": 0.0, "BIAS_FAIRNESS": 0.0, "ROBUSTNESS": 0.8333, "GOVERNANCE": 0.0},
        {"seed": 3, "are_total": 1, "OPS": 0.0, "BIAS_FAIRNESS": 0.0, "ROBUSTNESS": 0.8333, "GOVERNANCE": 0.0},
        {"seed": 4, "are_total": 1, "OPS": 0.0, "BIAS_FAIRNESS": 0.0, "ROBUSTNESS": 0.8333, "GOVERNANCE": 0.0},
    ]

    measured_refs = [ref for ref in envelope["evidence_refs"] if ref["verdict"] != "n/a"]
    na_refs = [ref for ref in envelope["evidence_refs"] if ref["verdict"] == "n/a"]
    assert len(measured_refs) == 8
    assert len(na_refs) == 17
    assert {ref["rubric_point_id"] for ref in measured_refs} == set(summary["rubric_point_id"])
    assert {ref["rubric_point_id"] for ref in na_refs} == set(envelope["uncovered_dimensions"])
    for ref in measured_refs:
        uri = ref["artifact_uri"]
        assert "placeholder" not in uri
        assert "r1-shelf-build" not in uri
        assert re.match(r"^are-probe://[^/]+/.+/sha256:[0-9a-f]{64}$", uri), uri
    assert summary["rpii_placeholder_present"] is False

    decision = summary["admittance_decision"]
    assert decision["admittance_class"] == "REFUSE"
    assert decision["firing_rule_id"] == "ADM.R0"
    assert decision["measured_dims_count"] == 8
    assert decision["na_dims_count"] == 17
    assert summary["quote_result"]["status"] == "ineligible"
    assert summary["quote_result"]["rule_id"] == "ADM.R0"
    assert summary["golden_delta"]["status"] == "pass"
