"""Tests for starter-100@5 pack (BEAAA-39974)."""

import pytest

from scanners.starter_pack_v5 import (
    STARTER_PACK_V5_ID,
    STARTER_PACK_V5_TEST_COUNT,
    STARTER_V5_CASES,
    StarterCaseV5MT,
)
from scanners.starter_pack_v2 import StarterCaseV2


EXPECTED_FAMILY_COUNTS = {
    "prompt_injection": 14,
    "secrets_pii_leak": 14,
    "out_of_scope_actions": 14,
    "made_up_facts": 14,
    "refusal_escalation": 14,
    "answers_normal_requests": 8,
    "jailbreak_roleplay": 6,
    "discrimination": 6,
    "system_prompt_leak": 6,
    "harmful_content": 4,
}


def test_total_case_count():
    assert len(STARTER_V5_CASES) == 100
    assert STARTER_PACK_V5_TEST_COUNT == 100


def test_pack_id():
    assert STARTER_PACK_V5_ID == "starter-100@5"


def test_family_counts():
    from collections import Counter
    counts = Counter(c.family_id for c in STARTER_V5_CASES)
    for family, expected in EXPECTED_FAMILY_COUNTS.items():
        assert counts[family] == expected, (
            f"Family {family!r}: expected {expected}, got {counts[family]}"
        )


def test_all_case_ids_unique():
    ids = [c.case_id for c in STARTER_V5_CASES]
    assert len(ids) == len(set(ids)), "Duplicate case_ids found"


def test_single_turn_cases_have_nonempty_prompt():
    for case in STARTER_V5_CASES:
        if isinstance(case, StarterCaseV2):
            assert case.prompt.strip(), f"Empty prompt on {case.case_id}"


def test_refusal_escalation_cases_have_multiturn():
    for case in STARTER_V5_CASES:
        if case.family_id == "refusal_escalation":
            assert isinstance(case, StarterCaseV5MT), (
                f"{case.case_id} is refusal_escalation but not StarterCaseV5MT"
            )
            assert len(case.turns) >= 2, (
                f"{case.case_id} must have at least 2 turns, got {len(case.turns)}"
            )


def _read_app_py_text() -> str:
    import os
    path = os.path.join(
        os.path.dirname(__file__), "..", "backbond", "scan-intake", "server", "app.py"
    )
    with open(path) as f:
        return f.read()


def test_pack_id_selectable_from_allowed_ids():
    """starter-100@5 must appear in app.py STARTER_PACK_IDS literal."""
    src = _read_app_py_text()
    assert '"starter-100@5"' in src, "starter-100@5 not in app.py STARTER_PACK_IDS"


def test_default_pack_id_is_not_v5():
    src = _read_app_py_text()
    # DEFAULT_STARTER_PACK_ID must not point to @5
    import re
    m = re.search(r'DEFAULT_STARTER_PACK_ID\s*=\s*"([^"]+)"', src)
    assert m is not None, "DEFAULT_STARTER_PACK_ID not found in app.py"
    assert m.group(1) != "starter-100@5"
