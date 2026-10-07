"""Pins the starter-100@3 roster (BEAAA-38898): @2 with unsafe_output swapped
for answers_normal_requests."""

import pytest

from scanners.starter_pack_v2 import STARTER_V2_CASES, STARTER_V2_FAMILIES
from scanners.starter_pack_v3 import (
    ANSWERS_NORMAL_REQUESTS,
    STARTER_PACK_V3_ID,
    STARTER_PACK_V3_TEST_COUNT,
    STARTER_V3_CASES,
    STARTER_V3_FAMILIES,
    STARTER_V3_RUBRICS,
)

# Same rule as @2: no word that tells the agent it is being scanned.
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
    "answers_normal_requests",
)

ANSWER_CASES = [c for c in STARTER_V3_CASES if c.family_id == ANSWERS_NORMAL_REQUESTS]


def test_pack_identity():
    assert STARTER_PACK_V3_ID == "starter-100@3"
    assert STARTER_PACK_V3_TEST_COUNT == 100


def test_ten_families_unsafe_output_swapped():
    assert tuple(f[0] for f in STARTER_V3_FAMILIES) == _EXPECTED_FAMILY_IDS
    assert "unsafe_output" not in STARTER_V3_RUBRICS
    for _family_id, label, rubric in STARTER_V3_FAMILIES:
        assert label.strip()
        assert "PASS:" in rubric and "FAIL:" in rubric


def test_other_nine_families_are_v2_verbatim():
    v2 = {f[0]: f for f in STARTER_V2_FAMILIES}
    for family in STARTER_V3_FAMILIES[:9]:
        assert family == v2[family[0]]
    v2_cases = [c for c in STARTER_V2_CASES if c.family_id != "unsafe_output"]
    assert list(STARTER_V3_CASES[:90]) == v2_cases


def test_exactly_100_cases_10_per_family_distinct():
    assert len(STARTER_V3_CASES) == 100
    counts: dict[str, int] = {}
    for case in STARTER_V3_CASES:
        counts[case.family_id] = counts.get(case.family_id, 0) + 1
    assert counts == {f: 10 for f in _EXPECTED_FAMILY_IDS}
    assert len({c.prompt for c in STARTER_V3_CASES}) == 100
    assert len({(c.family_id, c.case_id) for c in STARTER_V3_CASES}) == 100


def test_answers_family_shape():
    assert [c.case_id for c in ANSWER_CASES] == [f"ANS-{i:02d}" for i in range(1, 11)]
    for case in ANSWER_CASES:
        assert case.canary is None
        assert case.rubric_point_id == "P1.4"
        assert "\nUser:" not in case.prompt and "\nAssistant:" not in case.prompt


def test_answers_rubric_fails_refusal_and_deflection():
    rubric = STARTER_V3_RUBRICS[ANSWERS_NORMAL_REQUESTS].lower()
    fail_side = rubric.split("fail:", 1)[1]
    for phrase in ("refuses", "deflects", "authorised person"):
        assert phrase in fail_side


@pytest.mark.parametrize("case", ANSWER_CASES, ids=lambda c: c.case_id)
def test_no_banned_substrings_in_new_prompts(case):
    low = case.prompt.lower()
    assert not [b for b in _BANNED_SUBSTRINGS if b in low]
