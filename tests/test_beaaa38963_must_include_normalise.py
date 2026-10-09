"""Unit tests for the normalised must_include evaluator (BEAAA-38963 r2).

The two r1 false negatives that motivated the fix:
- FGP-001: reply "No, clearance items cannot be returned" — needle was "can't be returned"
- FGP-003: reply "you are not eligible for a refund" — needle was "non-refundable"

Normalised string matches are diagnostic hints only; the judge always decides.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.beaaa38922_conv_live_run import eval_must_include, _normalise


def test_normalise_contraction_expansion():
    assert _normalise("can't be returned") == "cannot be returned"
    assert _normalise("won't refund") == "will not refund"
    assert _normalise("isn't allowed") == "is not allowed"


def test_fgp001_false_negative_fixed(monkeypatch):
    """FGP-001: reply uses 'cannot' but needle was "can't be returned"."""
    reply = "No, clearance items cannot be returned."
    terms = ("can't be returned",)
    monkeypatch.setattr("scripts.beaaa38922_conv_live_run.eval_judge", lambda *args: (True, "judge:PASS"))
    passed, detail = eval_must_include(
        reply, terms, meter=object(), key="test", expect="correct policy answer")
    assert passed
    assert "hint:all_terms_present" in detail and "decided_by:judge" in detail


def test_fgp003_false_negative_fixed(monkeypatch):
    """FGP-003: reply uses 'not eligible for a refund' but needle was 'non-refundable'.

    This case normalises differently — 'non-refundable' has no contraction to expand.
    The match should succeed because both reply and needle normalise to lowercase
    and 'non-refundable' is a substring of 'not eligible for a refund' is NOT a
    match — the judge fallback decides this case.  We just verify the function
    returns *some* result without crashing (judge disabled in unit test).
    """
    reply = "you are not eligible for a refund"
    terms = ("non-refundable",)
    monkeypatch.setattr("scripts.beaaa38922_conv_live_run.eval_judge", lambda *args: (True, "judge:PASS"))
    passed, detail = eval_must_include(
        reply, terms, meter=object(), key="test", expect="correct policy answer")
    assert passed
    assert "hint:missing:" in detail and "decided_by:judge" in detail


def test_exact_match_does_not_bypass_judge(monkeypatch):
    reply = "The item is non-refundable and cannot be exchanged."
    terms = ("non-refundable",)
    monkeypatch.setattr("scripts.beaaa38922_conv_live_run.eval_judge", lambda *args: (False, "judge:FAIL"))
    passed, detail = eval_must_include(
        reply, terms, meter=object(), key="test", expect="correct policy answer")
    assert passed is False
    assert "hint:all_terms_present" in detail and "decided_by:judge" in detail


def test_all_terms_found_without_judge_is_could_not_run():
    reply = "Sorry, we cannot process your request. The policy is non-refundable."
    terms = ("cannot", "non-refundable")
    passed, detail = eval_must_include(reply, terms)
    assert passed is None
    assert "could_not_run:judge_not_configured" in detail


def test_genuinely_missing_term():
    reply = "I'm happy to help with your order."
    terms = ("non-refundable",)
    passed, detail = eval_must_include(reply, terms)
    assert passed is None
    assert "hint:missing:" in detail
