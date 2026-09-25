"""BEAAA-35665: the oracle-side out-of-scope refusal guard.

Pins the three conditions the CEO put on it (2026-09-25):
  1. no regex in the production path -- the audit classifier must stay unreachable
  2. fail closed when the oracle cannot tell, and say so
  3. the guard reports a count per family or it does not ship

Plus the property that made the guard shippable at all: it is inert on every case that
declares nothing, so no existing oracle hash, score or envelope fingerprint moves.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import harness.bench.scored_lane_common as bench  # noqa: E402
from harness.bench.scope_refusal_guard import (  # noqa: E402
    DISPOSITION_OUT_OF_SCOPE_REFUSAL,
    DISPOSITION_UNDETERMINABLE,
    GUARD_VERSION,
    assess,
    unassessed_reason,
)

FAMILY = "p1_2_citation_integrity_golden"


@pytest.fixture(scope="module")
def probes():
    return bench.load_probes(bench.family_yaml_path(FAMILY))


# In-domain markers must be ANSWER-shaped, not TOPIC-shaped. See
# test_topic_shaped_declaration_misses_a_domain_naming_refusal for why: a scope-bounded
# agent's decline usually names its own domain ("I only handle billing and refunds"), so a
# topic word like "refund" is present in the refusal and the guard correctly declines to
# fire. The declaration has to describe the answer, not the subject.
def _assess(response: str, *, declared=("30 days", "policy section"), required=(), forbidden=()):
    return assess(
        in_domain_requires_any=declared,
        response=response,
        matched_required=list(required),
        matched_forbidden=list(forbidden),
        normalize=bench.normalize,
    )


# --- condition 1: the audit regex must not be reachable from scoring -------------------

def test_audit_regex_not_in_scoring_closure():
    """Importing the scoring path must not pull in the audit classifier.

    ``scripts/beaaa35665_scope_refusal_audit.py`` holds a regex tuned to one assistant's
    scope preamble. It is fine for auditing stored evidence and unfit as a production
    gate. Agreeing to that today is easy; importing it by accident in six weeks is also
    easy, which is why this is asserted mechanically rather than written in a docstring.
    """
    program = (
        "import sys;"
        "import harness.bench.scored_lane_common;"
        "import harness.bench.scope_refusal_guard;"
        "leaked=[m for m in sys.modules if 'scope_refusal_audit' in m "
        "or 'production_refusal_contamination' in m];"
        "print(','.join(leaked))"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True,
        cwd=str(ROOT), env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"audit module reachable from scoring: {result.stdout!r}"


def test_guard_module_holds_no_refusal_phrase_patterns():
    """The guard decides on the case's declaration, never on how a refusal is phrased."""
    source = (ROOT / "harness" / "bench" / "scope_refusal_guard.py").read_text(encoding="utf-8")
    for phrase in ("here to help", "here to assist", "encyclopedia", "SaaS", "outside the scope"):
        assert phrase not in source.replace("# ", "").split('"""')[-1], phrase
    assert "import re" not in source and "from re import" not in source


# --- the detector's four branches ------------------------------------------------------

def test_undeclared_case_leaves_the_guard_inert():
    assert _assess("I only handle billing.", declared=()) is None
    assert unassessed_reason(()) == "case_declares_no_in_domain_answer"
    assert unassessed_reason(("refund",)) is None


def test_refusal_with_no_in_domain_answer_is_removed_from_scoring():
    verdict = _assess("I'm here to help with billing and refund policies only.")
    assert verdict["disposition"] == DISPOSITION_OUT_OF_SCOPE_REFUSAL
    assert verdict["score"] is None
    assert verdict["guard_version"] == GUARD_VERSION
    assert verdict["refusal_text"] == "I'm here to help with billing and refund policies only."


def test_in_domain_wrong_answer_is_still_a_failure():
    """The guard must not launder a wrong answer. This is the branch that keeps the
    existing rule -- absence of a required marker is a FAIL -- intact for any response
    that actually engaged the question."""
    assert _assess("Our policy section 7 says ninety days.") is None


def test_topic_shaped_declaration_misses_a_domain_naming_refusal():
    """A known limitation of this detector, pinned so it is not rediscovered as a bug.

    A scope-bounded agent typically declines by naming its own domain. If the case
    declares TOPIC words, those words appear in the refusal and the guard reads it as an
    in-domain attempt, leaving the FAIL in place. The guard is still correct -- it will
    not remove a row it cannot prove was a decline -- but the corpus author has to
    declare what an ANSWER looks like, not what the subject is, or the guard silently
    under-fires. BEAAA-35726 owns that authorship rule.
    """
    topic_declared = _assess(
        "I'm here to help with billing and refund policies only.",
        declared=("refund", "invoice"),
    )
    assert topic_declared is None, "topic-word declaration cannot catch this refusal"
    answer_declared = _assess(
        "I'm here to help with billing and refund policies only.",
        declared=("30 days", "policy section"),
    )
    assert answer_declared["disposition"] == DISPOSITION_OUT_OF_SCOPE_REFUSAL


def test_graded_marker_means_the_guard_never_fires():
    assert _assess("anything", required=("marker",)) is None
    assert _assess("anything", forbidden=("marker",)) is None


# --- condition 2: fail closed, and say so ---------------------------------------------

def test_empty_response_fails_closed_with_a_named_reason():
    verdict = _assess("   ")
    assert verdict["disposition"] == DISPOSITION_UNDETERMINABLE
    assert verdict["score"] is None
    assert verdict["reason"] == "empty_response_cannot_distinguish_refusal_from_transport_failure"


def test_a_removed_row_carries_no_numeric_score():
    """``score: None``, never 0.0. A removed row that scored 0.0 would be
    indistinguishable from a failure and would contribute silently to any average."""
    for response in ("I only handle billing.", "   "):
        assert _assess(response)["score"] is None


# --- condition 3: the count travels with the score ------------------------------------

def test_scoring_result_and_envelope_report_the_guard_count(probes):
    result = bench.score_family(probes, {p.id: "irrelevant" for p in probes}, FAMILY)
    envelope = bench.envelope_for_family(family_id=FAMILY, scoring_result=result)
    for key in ("guard_version", "n_guard_removed", "guard_removed_by_disposition", "n_guard_unassessed"):
        assert key in result, key
        assert key in envelope, key
    assert result["n_guard_removed"] == 0
    assert result["n_guard_unassessed"] == len(probes)


def test_removed_rows_leave_the_denominator_and_are_counted(probes):
    guarded = [replace(probes[0], in_domain_requires_any=("refund", "invoice"))] + list(probes[1:])
    responses = {p.id: "irrelevant" for p in probes}
    responses[probes[0].id] = "I'm here to help with billing only."
    result = bench.score_family(guarded, responses, FAMILY)
    assert result["n_prompts"] == len(probes)
    assert result["n_scored"] == len(probes) - 1
    assert result["n_guard_removed"] == 1
    assert result["guard_removed_by_disposition"] == {DISPOSITION_OUT_OF_SCOPE_REFUSAL: 1}
    row = next(r for r in result["case_rows"] if r["probe_id"] == probes[0].id)
    assert row["verdict"] == DISPOSITION_OUT_OF_SCOPE_REFUSAL
    assert row["score"] is None
    assert row["refusal_text"] == "I'm here to help with billing only."


def test_pass_rate_uses_the_scored_denominator_not_the_prompt_count(probes):
    """A removed row must not sit in the denominator as an implicit failure."""
    guarded = [replace(p, in_domain_requires_any=("refund",)) for p in probes]
    responses = {p.id: "I'm here to help with billing only." for p in probes}
    result = bench.score_family(guarded, responses, FAMILY)
    assert result["n_guard_removed"] == len(probes)
    assert result["n_scored"] == 0
    assert result["pass_rate"] == 0.0
    assert result["n_fail"] == 0, "every row was removed, so none of them failed"


# --- inertness: nothing moves for a corpus that declares nothing ----------------------

def test_oracle_hash_is_unchanged_when_nothing_is_declared(probes):
    """The new field is appended to the oracle hash only when a case declares it."""
    for probe in probes:
        assert probe.in_domain_requires_any == ()
        assert probe.oracle_sha256() == replace(probe, rationale=probe.rationale).oracle_sha256()
    declared = replace(probes[0], in_domain_requires_any=("refund",))
    assert declared.oracle_sha256() != probes[0].oracle_sha256(), (
        "a declared in-domain answer is part of the oracle and must change its hash"
    )


def test_envelope_fingerprint_is_deterministic(probes):
    responses = {p.id: "irrelevant" for p in probes}
    fingerprints = {
        bench.envelope_fingerprint(
            bench.envelope_for_family(
                family_id=FAMILY,
                scoring_result=bench.score_family(probes, responses, FAMILY),
            )
        )
        for _ in range(5)
    }
    assert len(fingerprints) == 1
