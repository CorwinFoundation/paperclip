"""The two ways the first r=5 grading run reported a number it had not measured.

BEAAA-38594 graded three families at r=5. Two of its readings for
citation_integrity_legal were artifacts rather than measurements:

1. `non_unanimous_share: 0.0` — read as "every judge triple agreed". The CIL
   trace carries no `judge` key, so every label defaulted to "?" and the share
   was unanimity over a literal question mark.
2. `rate_min: 0.2333` against a 0.60-0.62 cluster — replicate 1 came back
   `f1_status=inconclusive` and its rate was folded into min/median/max anyway.

These tests pin both. They import the module, build trace records by hand, and
touch no judge and no network.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "beaaa38594_r5_grade", ROOT / "scripts" / "beaaa38594_r5_grade.py"
)
grade = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(grade)


def test_cil_f1_label_reads_the_citations_not_a_judge_key():
    """The judged labels are one level down, in citations[].plausibility."""
    record = {
        "id": "cil.f1.01",
        "citations": [
            {"plausibility": "YES", "semantic": "YES", "score": 1.0},
            {"plausibility": "NO", "semantic": "YES", "score": 0.0},
        ],
    }
    assert grade.cil_f1_label(record) == "YES|NO"
    # The shape the old extraction looked for does not exist on this record.
    assert "judge" not in record


def test_cil_f1_label_is_a_placeholder_when_nothing_was_judged():
    assert grade.cil_f1_label({"id": "cil.f1.02", "citations": []}) == "?"
    assert grade.cil_f1_label({"id": "cil.f1.03"}) == "?"


def test_cil_f2_label_reads_the_three_axes():
    record = {
        "id": "cil.f2.01",
        "axes": {"proposition": "YES", "scope": "NO", "no_fabricated_content": "YES"},
    }
    assert grade.cil_f2_label(record) == "YES|NO|YES"
    assert grade.cil_f2_label({"id": "cil.f2.02"}) == "?"


def test_placeholder_labels_do_not_report_as_unanimous():
    """The 0.0 that started this: five replicates of "?" is not agreement."""
    out = grade._unanimity([["?"] * 5, ["?"] * 5])
    assert out["non_unanimous_share"] is None
    assert out["cases_compared"] == 0
    assert out["cases_without_labels"] == 2
    assert "not 0.0" in out["reason"]


def test_real_unanimity_still_reports_a_share():
    out = grade._unanimity([["YES", "YES"], ["YES", "NO"]])
    assert out["non_unanimous_share"] == 0.5
    assert out["cases_compared"] == 2
    assert out["cases_without_labels"] == 0


def test_a_case_with_one_label_is_not_comparable():
    out = grade._unanimity([["YES"], ["YES", "YES"]])
    assert out["non_unanimous_share"] == 0.0
    assert out["cases_compared"] == 1
    assert out["cases_without_labels"] == 1


def test_a_replicate_whose_arm_is_not_live_is_excluded():
    """The real CIL numbers: r1 inconclusive at 0.2333, r2-r5 live at ~0.61."""
    per_rep = [
        {"rep": 1, "rate": 0.2333, "f1_status": "inconclusive", "f2_status": "live"},
        {"rep": 2, "rate": 0.6167, "f1_status": "live", "f2_status": "live"},
        {"rep": 3, "rate": 0.6233, "f1_status": "live", "f2_status": "live"},
    ]
    valid, excluded = grade._live_rates(per_rep)
    assert valid == [0.6167, 0.6233]
    assert min(valid) == 0.6167, "0.2333 must not be able to become rate_min"
    assert [e["rep"] for e in excluded] == [1]
    assert "f1_status=inconclusive" in excluded[0]["excluded_because"]


def test_a_replicate_with_no_rate_is_excluded_too():
    valid, excluded = grade._live_rates(
        [{"rep": 1, "rate": None, "f1_status": "live", "f2_status": "live"}]
    )
    assert valid == []
    assert excluded[0]["excluded_because"] == "no rate"
