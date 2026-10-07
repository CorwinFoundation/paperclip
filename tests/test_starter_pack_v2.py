"""Pins the starter-100@2 frozen case roster (BEAAA-38894)."""

import re

import pytest

from scanners.starter_pack_v2 import (
    STARTER_PACK_V2_ID,
    STARTER_PACK_V2_TEST_COUNT,
    STARTER_V2_CASES,
    STARTER_V2_FAMILIES,
    StarterCaseV2,
)

# Words a real user or attacker would never put in a genuine request, and which
# would leak that the prompt is part of a scan or hand the agent the answer.
_BANNED_SUBSTRINGS = ("backbond", "test", "synthetic", "scenario", "evaluation")

_EXPECTED_FAMILY_IDS = (
    "prompt_injection",
    "system_prompt_leak",
    "secrets_pii_leak",
    "discrimination",
    "made_up_facts",
    "out_of_scope_actions",
    "harmful_content",
    "jailbreak_roleplay",
    "refusal_escalation",
    "unsafe_output",
)


def test_pack_identity():
    assert STARTER_PACK_V2_ID == "starter-100@2"
    assert STARTER_PACK_V2_TEST_COUNT == 100


def test_ten_families_in_order():
    assert tuple(f[0] for f in STARTER_V2_FAMILIES) == _EXPECTED_FAMILY_IDS
    for family_id, label, rubric in STARTER_V2_FAMILIES:
        assert label.strip()
        # A rubric states both the pass and the fail side.
        assert "PASS:" in rubric and "FAIL:" in rubric


def test_exactly_100_cases_10_per_family():
    assert len(STARTER_V2_CASES) == 100
    counts: dict[str, int] = {}
    for case in STARTER_V2_CASES:
        assert isinstance(case, StarterCaseV2)
        counts[case.family_id] = counts.get(case.family_id, 0) + 1
    assert set(counts) == set(_EXPECTED_FAMILY_IDS)
    assert all(n == 10 for n in counts.values())


def test_case_ids_and_rubric_points_present():
    seen_ids: set[tuple[str, str]] = set()
    for case in STARTER_V2_CASES:
        key = (case.family_id, case.case_id)
        assert key not in seen_ids, f"duplicate case id {key}"
        seen_ids.add(key)
        assert re.fullmatch(r"[A-Z]+-\d{2}", case.case_id), case.case_id
        assert re.fullmatch(r"P\d+\.\d+", case.rubric_point_id), case.rubric_point_id


def test_all_100_prompts_distinct():
    prompts = [c.prompt for c in STARTER_V2_CASES]
    assert len(set(prompts)) == 100


@pytest.mark.parametrize("case", STARTER_V2_CASES, ids=lambda c: f"{c.family_id}:{c.case_id}")
def test_no_banned_substrings_in_prompt(case: StarterCaseV2):
    low = case.prompt.lower()
    hits = [b for b in _BANNED_SUBSTRINGS if b in low]
    assert not hits, f"{case.case_id} leaks banned substring(s) {hits}"


def test_prompts_are_single_turn_nonempty():
    for case in STARTER_V2_CASES:
        assert case.prompt.strip(), case.case_id
        # Single-turn: no chat-role markers that would imply a scripted dialogue.
        assert "\nUser:" not in case.prompt and "\nAssistant:" not in case.prompt


def test_canary_when_present_appears_in_prompt():
    # A canary is only a deterministic fail signal if it is actually planted in
    # the prompt the agent sees.
    for case in STARTER_V2_CASES:
        if case.canary is not None:
            assert case.canary.strip()
            assert case.canary in case.prompt, case.case_id


def test_canary_coverage_on_deterministic_families():
    # The spec asks for canaries on most prompt_injection, system_prompt_leak and
    # secrets_pii_leak cases.
    for family_id in ("prompt_injection", "system_prompt_leak", "secrets_pii_leak"):
        rows = [c for c in STARTER_V2_CASES if c.family_id == family_id]
        with_canary = [c for c in rows if c.canary is not None]
        assert len(with_canary) >= 5, f"{family_id}: only {len(with_canary)}/10 carry a canary"
