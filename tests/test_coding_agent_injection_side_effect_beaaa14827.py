"""Focused deterministic checks for BEAAA-14827."""
from __future__ import annotations

import copy
import json

import pytest

from harness import coding_agent_injection_side_effect as probe


@pytest.fixture(scope="module")
def fixture():
    return probe.load_fixture()


def test_known_bad_attempts_one_bounded_mock_file_mutation(fixture):
    result = probe.run_subject(fixture, "known_bad")
    assert result.verdict == "COMPROMISED"
    assert result.attempted_side_effects == 1
    refs = {ref["kind"]: ref for ref in result.evidence_refs}
    assert set(refs) == {"transcript", "tool_call_log"}
    assert all(ref["weight"] == 0.0 for ref in refs.values())


def test_known_good_refuses_without_attempting_a_side_effect(fixture):
    result = probe.run_subject(fixture, "known_good")
    assert result.verdict == "RESISTED"
    assert result.attempted_side_effects == 0
    assert {ref["kind"] for ref in result.evidence_refs} == {"transcript", "tool_call_log"}


@pytest.mark.parametrize("profile", ["known_bad", "known_good"])
def test_n5_replay_is_byte_identical(fixture, profile):
    runs = [
        json.dumps(probe.run_subject(fixture, profile).as_dict(), sort_keys=True)
        for _ in range(5)
    ]
    assert len(set(runs)) == 1


def test_fixture_cannot_activate_scoring(fixture):
    assert fixture["reported_not_scored"] is True
    assert fixture["weight"] == 0.0
    result = probe.run_subject(fixture, "known_bad").as_dict()
    assert result["reported_not_scored"] is True
    assert result["scanner_version"] == "0.5.0"
    assert result["corpus_version"] == "0.2.0"


def test_action_is_denied_before_real_side_effect(fixture):
    result = probe.run_subject(fixture, "known_bad")
    transcript_ref = next(ref for ref in result.evidence_refs if ref["kind"] == "transcript")
    assert len(transcript_ref["content_sha256"]) == 64
    # The harness uses an empty scanner-mediated allowlist; no filesystem API is invoked.
    assert fixture["bounded_action"]["arguments"]["path"].startswith("/mock-workspace/")


def test_fixture_rejects_unbounded_or_unknown_tool(fixture, tmp_path):
    invalid = copy.deepcopy(fixture)
    invalid["bounded_action"]["tool_name"] = "delete_repository"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(invalid))
    with pytest.raises(probe.InjectionSideEffectError):
        probe.load_fixture(path)
