"""BEAAA-30934 work item 3: a verdict-less row must state its own cause.

``_case_outcome`` turns ``score is None`` into ``verdict="unresolved"``. Nothing
recorded *why*, so the row reached the scheduled-scan manifest with
``incomplete_reason: None`` and ``_incomplete_group`` — the reason coder the
card points at — could only bucket it as ``unclassified``. In the readable
BEAAA-33199 regrade of the same target that is 19 of the 21 unresolved rows:
``p1_5_calibration_golden`` (7) and ``tool_misuse_param_tamper`` (12), every one
of them ``status="complete"``, ``target_calls>=1``, reason ``None``.

``p1_5_calibration_golden`` is the case worth separating. ``calibration_score``
returns ``None`` for two unrelated reasons — the target stated no parseable
confidence, or the judge returned INCONCLUSIVE — which are a format defect and
a judging outcome. The card asks for exactly that split: "Format problem -> fix
the parser. Otherwise classify with a reason."

Nothing here changes a score, a verdict or a status, so the golden band cannot
move; ``test_a_resolved_outcome_is_byte_identical_to_before`` pins that.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.scheduled_scan import _incomplete_group
from tools.registered_case_executor import _case_outcome

BASE = dict(payloads=["p"], responses=["r"], judge={"type": "t"}, expected={"e": 1})


# --- the outcome record ---------------------------------------------------


def test_an_unresolved_outcome_without_a_supplied_reason_still_names_one():
    outcome = _case_outcome(**BASE, score=None)
    assert outcome["verdict"] == "unresolved"
    assert outcome["unresolved_reason"] == "score_unavailable:adapter_supplied_no_reason"


def test_a_supplied_reason_is_carried_verbatim():
    outcome = _case_outcome(
        **BASE, score=None, unresolved_reason="calibration_confidence_unparsed"
    )
    assert outcome["unresolved_reason"] == "calibration_confidence_unparsed"


@pytest.mark.parametrize(
    "score,verdict", [(1.0, "pass"), (0.0, "fail"), (0.5, "partial")]
)
def test_a_resolved_outcome_is_byte_identical_to_before(score, verdict):
    """Delta-zero: the new key exists only on a row that had no verdict."""

    outcome = _case_outcome(**BASE, score=score)
    assert outcome["verdict"] == verdict
    assert "unresolved_reason" not in outcome
    # BEAAA-33126 landed alongside this and adds one more always-present key,
    # ``reported_not_scored``, so that a report-only row carries its marker in
    # one canonical place instead of three. Widened here, not weakened: the
    # claim this test makes is that ``unresolved_reason`` appears only on a
    # verdict-less row, and the assertion above still pins exactly that.
    assert set(outcome) == {
        "payloads", "responses", "expected", "judge", "score", "verdict",
        "reported_not_scored",
    }


def test_an_explicit_non_unresolved_verdict_gains_nothing_either():
    outcome = _case_outcome(**BASE, score=None, verdict="not_applicable")
    assert outcome["verdict"] == "not_applicable"
    assert "unresolved_reason" not in outcome


# --- the reason coder -----------------------------------------------------


def test_an_unparsed_confidence_is_a_format_problem():
    assert _incomplete_group("calibration_confidence_unparsed", target_calls=1) == "format"


def test_an_inconclusive_judge_is_not_a_missing_judge():
    """The substring 'judge' would otherwise route an answered judge to
    ``missing_judge`` and send an operator after a working credential."""

    assert (
        _incomplete_group("judge_correctness_inconclusive", target_calls=1)
        == "inconclusive_judgement"
    )


def test_the_unnamed_fallback_stays_unclassified():
    assert (
        _incomplete_group("score_unavailable:adapter_supplied_no_reason", target_calls=5)
        == "unclassified"
    )


@pytest.mark.parametrize(
    "reason,target_calls,group",
    [
        ("aborted_upstream:quota", 0, "aborted_upstream"),
        ("not_applicable:no mcp surface", 0, "unavailable_capability"),
        ("upstream_http_429", 1, "upstream_http"),
        ("garak_sample_size_below_floor", 1, "insufficient_sample"),
        ("anything", 0, "dispatch_failure"),
        ("missing_judge_credential", 1, "missing_judge"),
        ("worker_error", 1, "unavailable_capability"),
        ("transport_timeout", 1, "transport"),
        ("case_execution_error:RunnerError", 1, "format"),
        ("something novel", 1, "unclassified"),
    ],
)
def test_every_pre_existing_grouping_is_unchanged(reason, target_calls, group):
    assert _incomplete_group(reason, target_calls=target_calls) == group


# --- the calibration branch, end to end -----------------------------------


class _Probe:
    prompt = "What year?"
    gold_answer = "1999"
    judge_rubric_notes = "notes"
    case_id = "P15-001"


def _executor_branch(monkeypatch, response: str, correctness: str):
    """Drive the real p1_5 branch with a fake target and judge."""

    import tools.registered_case_executor as rce
    from harness.bench import p1_5_calibration_golden as bench

    monkeypatch.setattr(bench, "load_probes", lambda source: [_Probe()], raising=False)
    monkeypatch.setattr(
        bench, "judge_response", lambda *a, **k: {"correctness": correctness}
    )
    monkeypatch.setattr(rce, "_find_case", lambda probes, case_id: probes[0])

    captured = {}
    real = rce._case_outcome

    def spy(**kwargs):
        captured.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(rce, "_case_outcome", spy)

    class Subject:
        def complete(self, _prompt):
            return response

    executor = rce.RegisteredCaseExecutor.__new__(rce.RegisteredCaseExecutor)
    executor.subject = Subject()
    executor.judge = object()
    return executor, captured


def test_a_target_that_states_no_confidence_is_a_parse_problem(monkeypatch):
    executor, captured = _executor_branch(
        monkeypatch, "It was a very long time ago.", "CORRECT"
    )
    outcome = executor._execute_case(
        "p1_5_calibration_golden", "P15-001", {"source_pack": "harness/bench"}
    )
    assert outcome["verdict"] == "unresolved"
    assert outcome["unresolved_reason"] == "calibration_confidence_unparsed"
    assert _incomplete_group(outcome["unresolved_reason"], target_calls=1) == "format"


def test_an_inconclusive_judge_is_named_as_such(monkeypatch):
    executor, captured = _executor_branch(monkeypatch, "1999\n0.9", "INCONCLUSIVE")
    outcome = executor._execute_case(
        "p1_5_calibration_golden", "P15-001", {"source_pack": "harness/bench"}
    )
    assert outcome["verdict"] == "unresolved"
    assert outcome["unresolved_reason"] == "judge_correctness_inconclusive"


def test_a_scored_calibration_row_is_untouched(monkeypatch):
    executor, captured = _executor_branch(monkeypatch, "1999\n0.9", "CORRECT")
    outcome = executor._execute_case(
        "p1_5_calibration_golden", "P15-001", {"source_pack": "harness/bench"}
    )
    assert outcome["verdict"] == "pass"
    assert "unresolved_reason" not in outcome
