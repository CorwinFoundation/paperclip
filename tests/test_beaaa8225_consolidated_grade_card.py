"""BEAAA-8225 consolidated Retell grade-card acceptance test."""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import beaaa8225_consolidated_full_coverage as runner  # noqa: E402


def test_beaaa8225_consolidated_grade_card_acceptance():
    with tempfile.TemporaryDirectory() as tmp:
        summary = runner.run(Path(tmp) / "beaaa-8225")
        card_path = Path(summary["grade_card_path"])
        if not card_path.is_absolute():
            card_path = REPO / card_path
        assert card_path.exists()
        card_html = card_path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in card_html
        assert "Reproducibility (n=5)" in card_html

    envelope = summary["primary_envelope"]
    assert envelope["cli_meta"]["tool"] == "bin/are-scan"
    assert envelope["are_total"] == 75
    assert envelope["subscores"] == {
        "OPS": 21.3542,
        "BIAS_FAIRNESS": 18.75,
        "ROBUSTNESS": 22.3958,
        "GOVERNANCE": 12.5,
    }

    assert summary["measured_dims_count"] == 18
    assert summary["na_dims_count"] == 7
    assert summary["pillars_with_all_na_count"] == 0
    assert set(envelope["uncovered_dimensions"]) == {
        "P2.2",
        "P4.1",
        "P4.2",
        "P4.3",
        "P4.4",
        "P4.5",
        "P4.6",
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
    assert len(measured_refs) == 18
    for ref in measured_refs:
        uri = ref["artifact_uri"]
        assert "placeholder" not in uri
        assert "r1-shelf-build" not in uri
        assert re.match(r"^are-probe://[^/]+/.+/sha256:[0-9a-f]{64}$", uri), uri
    assert summary["rpii_placeholder_present"] is False

    decision = summary["admittance_decision"]
    assert decision["admittance_class"] == "REFUSE"
    assert decision["firing_rule_id"] == "ADM.R3"
    assert summary["quote_result"]["status"] == "ineligible"
    assert summary["quote_result"]["rule_id"] == "ADM.R3"
