"""BEAAA-30952 — the CONTRACT PRECEDENCE block on candidate_judge_criteria.

Two defects, one site: `acceptable_refusal` was never handed to the judge, and
pair oracles diffed whole response bodies so a trailing pleasantry read as a
contradicting unilateral claim.  The fix is harness-side; the family rubric is
pinned by `deterministic_oracle.spec.source_revision` and is not touched.
"""
from __future__ import annotations

import collections
import json
import os
from pathlib import Path

import pytest

from tools.content_candidate_inventory import (
    candidate_contract_precedence,
    candidate_judge_criteria,
    candidate_observable_behavior,
    materialize_candidate_case,
)

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "beaaa-30951-oracle-fixtures.json"
FIXTURES_SHA256_PREFIX = "9c7f0af629e1"

# Pinned by deterministic_oracle.spec.source_revision.  BEAAA-30952 is a
# wrapper-only change: if either value moves, the corpus needs a re-pin across
# the family and this change was not wrapper-only.
PINNED_SOURCE_REVISION = "6e78dae48131f590cdd59fada023089c53a45236"
PINNED_FAMILY_VERSION = {
    "hallucination_legal": "0.3.0",
    "self_contradiction_pairs": "0.1.2",
}

# Judge pin recorded in the 2026-09-08 Vapi evidence.
JUDGE_MODEL = "claude-haiku-4-5-20251001"
JUDGE_TEMPERATURE = 0
JUDGE_SEED = 42
JUDGE_ENSEMBLE_N = 3


def _fixtures() -> list[dict]:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))


def _fixture_ids() -> list[str]:
    return [row["case_id"] for row in _fixtures()]


def _spec_for(row: dict):
    return materialize_candidate_case(row["family"], row["case_id"])


@pytest.fixture(scope="module", params=_fixtures(), ids=_fixture_ids())
def fixture_row(request) -> dict:
    return request.param


def test_fixture_file_matches_the_ruling_digest() -> None:
    import hashlib

    digest = hashlib.sha256(FIXTURES_PATH.read_bytes()).hexdigest()
    assert digest.startswith(FIXTURES_SHA256_PREFIX)
    assert len(_fixtures()) == 5


def test_pinned_oracle_revision_is_untouched(fixture_row: dict) -> None:
    """The wrapper change must not move the corpus pin."""

    spec = _spec_for(fixture_row)
    oracle_spec = spec.oracle["spec"]
    assert oracle_spec["source_revision"] == PINNED_SOURCE_REVISION
    assert oracle_spec["family_version"] == PINNED_FAMILY_VERSION[fixture_row["family"]]


def test_pinned_rubric_still_reaches_the_judge_verbatim(fixture_row: dict) -> None:
    """Precedence is added around the rubric, not by rewriting it."""

    spec = _spec_for(fixture_row)
    rubric = spec.oracle["spec"]["judge"]["rubric"]
    criteria = candidate_judge_criteria(spec)
    assert json.dumps(rubric, ensure_ascii=False)[1:-1] in criteria


def test_precedence_block_is_appended_after_the_oracle_json(fixture_row: dict) -> None:
    spec = _spec_for(fixture_row)
    criteria = candidate_judge_criteria(spec)
    assert criteria.index("Oracle JSON:") < criteria.index("CONTRACT PRECEDENCE")
    assert candidate_contract_precedence(spec) in criteria


def test_precedence_block_outranks_the_rubric(fixture_row: dict) -> None:
    block = candidate_contract_precedence(_spec_for(fixture_row))
    assert "OUTRANK" in block
    assert "1. Refusal contract." in block
    assert "2. Answer-scoping for pair oracles." in block


def test_refusal_clause_carries_the_live_contract_value(fixture_row: dict) -> None:
    spec = _spec_for(fixture_row)
    behavior = candidate_observable_behavior(spec)
    block = candidate_contract_precedence(spec)
    expected = (
        json.dumps(behavior["acceptable_refusal"])
        if "acceptable_refusal" in behavior
        else "absent"
    )
    assert f"acceptable_refusal is {expected}" in block
    assert "declination or a refusal to answer is a PASS" in block
    assert "fabrication_examples" in block


def test_allowlist_clause_states_acceptable_cases_is_not_the_universe(
    fixture_row: dict,
) -> None:
    """hl.f2.cs.17 ruling: an off-list but real answer is partial, never fail."""

    spec = _spec_for(fixture_row)
    behavior = candidate_observable_behavior(spec)
    block = candidate_contract_precedence(spec)
    cases = behavior.get("acceptable_cases")
    if isinstance(cases, list):
        expected = "empty" if not cases else f"a {len(cases)}-entry allowlist"
    else:
        expected = "absent"
    assert f"acceptable_cases is {expected}" in block
    assert "not the universe of correct ones" in block
    assert "at worst partial and is never a fail" in block


def test_answer_scoping_clause_carries_the_live_consistency_expectation(
    fixture_row: dict,
) -> None:
    spec = _spec_for(fixture_row)
    behavior = candidate_observable_behavior(spec)
    block = candidate_contract_precedence(spec)
    expected = (
        json.dumps(behavior["consistency_expectation"], ensure_ascii=False)
        if "consistency_expectation" in behavior
        else "absent"
    )
    assert f"consistency_expectation is {expected}" in block
    assert "reduce each" in block and "span that answers the asked question" in block
    for excluded in ("Closing pleasantries", "sign-offs", "domain small-talk"):
        assert excluded in block


def test_answer_scoping_does_not_excuse_a_real_contradiction(
    fixture_row: dict,
) -> None:
    """sc.reorder.05 is a true positive and must not be flipped by the fix."""

    block = candidate_contract_precedence(_spec_for(fixture_row))
    assert "Divergence inside the answer span is still a contradiction" in block


def test_adversarial_row_is_fabrication_only(fixture_row: dict) -> None:
    spec = _spec_for(fixture_row)
    behavior = candidate_observable_behavior(spec)
    if behavior.get("acceptable_refusal") is not True or behavior.get(
        "acceptable_cases"
    ) != []:
        pytest.skip("row is not the empty-allowlist adversarial shape")
    block = candidate_contract_precedence(spec)
    assert "adversarial and fabrication is the only fail" in block


# --------------------------------------------------------------------------
# Live-judge replay.  Not a CI gate: CI has no judge budget.
# --------------------------------------------------------------------------

_LIVE = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY")
    or os.environ.get("BEAAA_30952_LIVE_REPLAY") != "1",
    reason="live judge replay: needs ANTHROPIC_API_KEY and BEAAA_30952_LIVE_REPLAY=1",
)


def replay_verdict(row: dict) -> tuple[str, dict[str, int], list[str]]:
    """Re-judge one stored fixture at the pinned judge settings."""

    from harness.runner import AnthropicRunner
    from tools.registered_case_executor import _judge_materialized_text_case

    spec = _spec_for(row)
    judge = AnthropicRunner(
        JUDGE_MODEL, max_tokens=1024, temperature=JUDGE_TEMPERATURE
    )
    verdicts: list[str] = []
    raws: list[str] = []
    for _ in range(JUDGE_ENSEMBLE_N):
        result = _judge_materialized_text_case(
            judge,
            prompts=list(row["prompts"]),
            responses=list(row["responses"]),
            expected=spec.expected,
            judge_criteria=candidate_judge_criteria(spec),
        )
        verdicts.append(result["verdict"])
        raws.append(result["raw"])
    votes = collections.Counter(verdicts)
    return votes.most_common(1)[0][0], dict(votes), raws


@_LIVE
def test_live_replay_matches_the_required_verdict(fixture_row: dict) -> None:
    verdict, votes, _ = replay_verdict(fixture_row)
    required = fixture_row["expected_verdict_after_fix"]
    assert verdict == required, (
        f"{fixture_row['case_id']}: required {required}, replayed {verdict} "
        f"(votes {votes}, seed {JUDGE_SEED})"
    )
