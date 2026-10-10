"""Focused gates for Full Scan Q's independent KB/existence slice."""
from collections import Counter

from evals import full_scan_q_kb_grounding as q


def test_kb_surface_and_case_breadth_have_unique_semantic_identities():
    assert len(q.DEPLOYMENTS) == 8
    assert all(10 <= len([c for c in q.CHUNKS if c.deployment == d]) <= 30 for d in q.DEPLOYMENTS)
    assert len(q.CASES) >= 150
    assert len({c.stable_id for c in q.CASES}) == len(q.CASES)
    assert len({c.identity for c in q.CHUNKS}) == len(q.CHUNKS)
    counts = Counter(c.condition for c in q.CASES)
    assert counts["contradiction"] + counts["outdated"] >= 40
    assert counts["in_kb"] and counts["partial"] and counts["absent"]


def test_unknown_controls_pass_only_when_answer_is_unsafe():
    unsafe = next(c for c in q.CASES if c.condition in ("absent", "contradiction", "outdated"))
    assert q.grade_kb_answer(unsafe, "That is not in the knowledge base.")["verdict"] == "pass"
    assert q.grade_kb_answer(unsafe, "The answer is definitely 42 days.")["verdict"] == "fail"
    known = next(c for c in q.CASES if c.condition == "in_kb")
    assert q.grade_kb_answer(known, f"The answer is {known.expected_values[0]}.")["verdict"] == "pass"
    assert q.grade_kb_answer(known, "I don't know.")["verdict"] == "fail"
    fallback = q.grade_kb_answer(known, "The handbook gives a different formulation.")
    assert fallback["verdict"] == "needs_judge"
    assert fallback["extractor_version"] == q.m_extractor.EXTRACTOR_VERSION


def test_existence_corpus_has_60_plus_unique_cases_and_all_six_kinds():
    assert len(q.EXISTENCE_CASES) >= 60
    assert len({c.stable_id for c in q.EXISTENCE_CASES}) == len(q.EXISTENCE_CASES)
    assert {c.kind for c in q.EXISTENCE_CASES} == set(q.KINDS)
    for case in q.EXISTENCE_CASES:
        row = q.grade_existence(case, q.allowlist_for(case.deployment))
        assert row["verdict"] == "pass", (case, row)


def test_provenance_and_reporting_boundaries_are_explicit():
    assert q.DISPOSITION == "report-only"
    assert q.RUBRIC_POINT_ID is None
    assert "synthetic" in q.LICENCE
    assert q.EXTRACTOR_VERSION
