import hashlib
import json
from pathlib import Path

import pytest

from scripts.adjudicate_saved_vapi_judges import adjudicate, saved_rows


def fixture(ordinal=1):
    text = "saved answer"
    evidence = {"records": [{
        "ordinal": ordinal, "stable_case_id": "case.1", "family_id": "family",
        "source_row_sha256": "a" * 64,
        "responses": [{"response_text": text, "response_sha256": hashlib.sha256(text.encode()).hexdigest()}],
        "judge_spec": {"spec": {"judge": {"temperature": 0}}},
        "prompt_action_reference": {"source_path": "rubric.yaml"},
    }]}
    scoreability = {"rows": [{
        "ordinal": ordinal,
        "blockers": ["judge_seed_unenforceable:anthropic_messages_api_has_no_seed_parameter"],
    }] * 295}
    return evidence, scoreability


def test_hash_mismatch_fails_closed():
    evidence, scoreability = fixture()
    scoreability["rows"] = [{**scoreability["rows"][0], "ordinal": i} for i in range(1, 296)]
    evidence["records"] = [{**evidence["records"][0], "ordinal": i} for i in range(1, 296)]
    evidence["records"][9]["responses"][0]["response_sha256"] = "bad"
    with pytest.raises(ValueError, match="hash mismatch"):
        saved_rows(evidence, scoreability)


def test_consensus_and_adjudication_are_separate_from_canonical():
    row = {"ordinal": 1, "stable_case_id": "case.1", "family_id": "family", "response_sha256": "b" * 64, "supportable": True}
    a = ("reviewer-a", {1: {"ordinal": 1, "label": "pass", "rationale": "rubric"}})
    b = ("reviewer-b", {1: {"ordinal": 1, "label": "fail", "rationale": "rubric"}})
    c = ("adjudicator", {1: {"ordinal": 1, "label": "partial", "rationale": "tie"}})
    result = adjudicate([row], a, b, c)
    assert result["statistics"]["adjudicated_rows"] == 1
    assert result["results"][0]["consensus_score"] == 0.5
    assert result["results"][0]["canonical_deterministic_result_unchanged"] is True


def test_disagreement_without_adjudicator_remains_unresolved():
    row = {"ordinal": 1, "stable_case_id": "case.1", "family_id": "family", "response_sha256": "b" * 64, "supportable": True}
    a = ("reviewer-a", {1: {"ordinal": 1, "label": "pass", "rationale": "x"}})
    b = ("reviewer-b", {1: {"ordinal": 1, "label": "fail", "rationale": "y"}})
    result = adjudicate([row], a, b, ("", {}))
    assert result["statistics"]["adjudicated_rows"] == 0
    assert result["statistics"]["unresolved_rows"] == 1
    assert result["unresolved_results"][0]["adjudicated_score_input_eligible"] is False


def test_named_adjudicator_abstention_is_preserved_row_by_row():
    row = {"ordinal": 1, "stable_case_id": "case.1", "family_id": "family", "response_sha256": "b" * 64, "supportable": True}
    abstain = {"ordinal": 1, "label": "abstain", "rationale": "frozen rubric excludes"}
    result = adjudicate(
        [row],
        ("reviewer-a", {1: abstain}),
        ("reviewer-b", {1: abstain}),
        ("adjudicator", {1: abstain}),
    )
    assert result["statistics"]["adjudicated_rows"] == 0
    assert result["statistics"]["unresolved_rows"] == 1
    assert result["unresolved_results"][0]["adjudicator"]["identity"] == "adjudicator"


def test_adjudicator_must_be_distinct_from_reviewers():
    row = {"ordinal": 1, "stable_case_id": "case.1", "family_id": "family", "response_sha256": "b" * 64, "supportable": True}
    pass_row = {"ordinal": 1, "label": "pass", "rationale": "x"}
    fail_row = {"ordinal": 1, "label": "fail", "rationale": "y"}
    with pytest.raises(ValueError, match="adjudicator identity"):
        adjudicate(
            [row],
            ("reviewer-a", {1: pass_row}),
            ("reviewer-b", {1: fail_row}),
            ("reviewer-a", {1: pass_row}),
        )


def test_hallucination_legal_embeds_prompt_and_ground_truth():
    evidence, scoreability = fixture()
    scoreability["rows"] = [
        {**scoreability["rows"][0], "ordinal": i} for i in range(1, 296)
    ]
    evidence["records"] = [
        {
            **evidence["records"][0],
            "ordinal": i,
            "family_id": "hallucination_legal" if i == 1 else "family",
            "prompt_action_reference": {"object_locator": "line:1"},
        }
        for i in range(1, 296)
    ]
    prompt_evidence = [{
        "prompt": "saved prompt",
        "ground_truth": "saved ground truth",
        "source_citation": "saved citation",
        "forbidden_behavior": "saved forbidden behavior",
        "scenario_version": "1.0.0",
        "authoring_ticket": "BEAAA-15688",
    }]

    row = saved_rows(evidence, scoreability, prompt_evidence)[0]

    assert row["embedded_prompt_ground_truth_evidence"]["prompt"] == "saved prompt"
    assert row["embedded_prompt_ground_truth_evidence"]["ground_truth"] == "saved ground truth"
    assert len(row["embedded_prompt_ground_truth_evidence"]["evidence_sha256"]) == 64
