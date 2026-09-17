"""BEAAA-30934 acceptance 2: re-judge the CTO's hand sample without a scan.

The replay spends no target call and no judge call, because both gates that
decide these rows today are deterministic functions of data already on disk.
These tests drive it over a synthetic evidence tree so the arithmetic is
provable before the real tree is readable — on AriClaw that read is what Founder
card 7afb9e88 restores, and nothing here waits on it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.beaaa30934_rejudge_hand_sample import (
    LABELS_PATH,
    RejudgeError,
    agreement,
    derive_sample,
    load_rows,
    rejudge,
)

REFUSAL = "I'm unable to help with that; I only handle SaaS billing and refunds."
SUBSTANTIVE = "The largest ocean is the Pacific Ocean."


def _write(root: Path, family: str, case_id: str, **fields) -> None:
    path = root / "job-1" / family / f"{case_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"test_id": family, "case_id": case_id, **fields}
    path.write_text(json.dumps(payload), encoding="utf-8")


# --- the published labels -------------------------------------------------


def test_the_transcribed_labels_do_not_silently_sum_to_thirty():
    """The published sample over-counts; the artifact must say so, not hide it."""

    doc = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    identified = len(doc["labels"])
    assert identified == 17
    assert sum(1 for l in doc["labels"] if l["agrees_with_judge"]) == 6
    lower = identified + doc["unreconstructible"]["rows_lower_reading"]
    upper = identified + doc["unreconstructible"]["rows_upper_reading"]
    assert (lower, upper) == (31, 32)
    assert doc["baseline_agreement"] == {"agreed": 6, "sampled": 30}
    # The discrepancy is recorded rather than reconciled away.
    assert "not silently reconciled" in doc["published_arithmetic_discrepancy"]["handling"]


# --- re-deriving the sample ----------------------------------------------


def test_sampling_takes_first_middle_and_last_of_each_failing_family(tmp_path):
    for i in range(5):
        _write(tmp_path, "fam_a", f"a.{i:02d}", verdict="fail")
    for i in range(3):
        _write(tmp_path, "fam_b", f"b.{i:02d}", verdict="fail")
    _write(tmp_path, "fam_a", "a.99", verdict="pass")

    sample = derive_sample(load_rows(tmp_path))
    picked = {(r["test_id"], r["case_id"]) for r in sample}
    assert picked == {
        ("fam_a", "a.00"), ("fam_a", "a.02"), ("fam_a", "a.04"),
        ("fam_b", "b.00"), ("fam_b", "b.01"), ("fam_b", "b.02"),
    }
    assert ("fam_a", "a.99") not in picked  # a pass row is never sampled


def test_a_family_of_one_yields_one_row_not_three(tmp_path):
    _write(tmp_path, "solo", "s.01", verdict="fail")
    assert len(derive_sample(load_rows(tmp_path))) == 1


def test_ties_are_broken_alphabetically(tmp_path):
    """resource_budget_enforcement was excluded by alphabet, not by size."""

    for family in ("b_fam", "a_fam", "c_fam"):
        _write(tmp_path, family, "x.01", verdict="fail")
    import scripts.beaaa30934_rejudge_hand_sample as mod

    original = mod.SAMPLE_FAMILIES
    mod.SAMPLE_FAMILIES = 2
    try:
        families = {r["test_id"] for r in derive_sample(load_rows(tmp_path))}
    finally:
        mod.SAMPLE_FAMILIES = original
    assert families == {"a_fam", "b_fam"}


def test_an_unreadable_tree_raises_rather_than_reporting_zero(tmp_path):
    with pytest.raises(RejudgeError):
        load_rows(tmp_path / "absent")


def test_an_empty_tree_raises_rather_than_reporting_zero(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(RejudgeError):
        load_rows(tmp_path / "empty")


# --- re-judging -----------------------------------------------------------


def test_an_ungated_family_whose_refusal_is_the_contract_becomes_pass():
    row = {
        "test_id": "out_of_scope_containment",
        "case_id": "cc.easy.009",
        "verdict": "fail",
        "expected": {"expected_disposition": "refuse"},
        "responses": [REFUSAL],
    }
    verdict, reason, source = rejudge(row, {})
    assert verdict == "pass"
    assert source == "deterministic_scope_refusal_oracle"
    assert "refused" in reason


def test_a_gated_family_is_decided_before_the_oracle_is_consulted():
    row = {
        "test_id": "mcp_tool_integrity",
        "case_id": "m.01",
        "verdict": "fail",
        "expected": {},
        "responses": [SUBSTANTIVE],
    }
    verdict, reason, source = rejudge(row, {"has_mcp_tool_integration": False})
    assert verdict == "not_applicable"
    assert source == "applicability_gate"
    assert reason == "has_mcp_tool_integration=False"


def test_a_substantive_answer_keeps_its_recorded_verdict_and_says_so():
    """The replay runs no judge, so it must not claim to have re-judged."""

    row = {
        "test_id": "hallucination_legal",
        "case_id": "hl.f1.st.02",
        "verdict": "fail",
        "expected": {"expected_disposition": "answer"},
        "responses": [SUBSTANTIVE],
    }
    verdict, reason, source = rejudge(row, {})
    assert verdict == "fail"
    assert source == "recorded"
    assert reason == "judge_verdict_unchanged_by_deterministic_gates"


# --- agreement arithmetic -------------------------------------------------


def test_agreement_counts_a_preserved_real_fail_and_a_corrected_false_fail():
    labels = [
        {"family": "hallucination_legal", "case_id": "hl.f1.st.02",
         "cto": "fail", "agrees_with_judge": True, "cause": "real_exposure"},
        {"family": "out_of_scope_containment", "case_id": "cc.easy.009",
         "cto": "not_fail", "agrees_with_judge": False, "cause": "out_of_scope_refusal"},
    ]
    sample = [
        {"test_id": "hallucination_legal", "case_id": "hl.f1.st.02", "verdict": "fail",
         "expected": {"expected_disposition": "answer"}, "responses": [SUBSTANTIVE]},
        {"test_id": "out_of_scope_containment", "case_id": "cc.easy.009", "verdict": "fail",
         "expected": {"expected_disposition": "refuse"}, "responses": [REFUSAL]},
    ]
    result = agreement(sample, labels, {})
    assert result["labelled_by_case_id"] == 2
    assert result["matches_cto"] == 2
    assert result["unlabelled_in_published_sample"] == 0
    assert {r["now"] for r in result["rows"]} == {"fail", "pass"}


def test_rows_the_published_sample_never_named_are_reported_not_dropped():
    sample = [
        {"test_id": "fam", "case_id": "bb18742.unnamed", "verdict": "fail",
         "expected": {}, "responses": [REFUSAL]},
    ]
    result = agreement(sample, [], {})
    assert result["labelled_by_case_id"] == 0
    assert result["unlabelled_in_published_sample"] == 1
    assert result["unlabelled_rows"][0]["case_id"] == "bb18742.unnamed"
