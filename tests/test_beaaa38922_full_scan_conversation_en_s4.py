"""BEAAA-38959: Full Scan conversation slice 4 is real, unique, runnable and apart from slices 1-3."""

import re
from collections import Counter

from scanners.full_scan_conversation_en import CASES
from scanners.full_scan_conversation_en_s2 import CASES_S2
from scanners.full_scan_conversation_en_s3 import CASES_S3
from scanners.full_scan_conversation_en_s4 import (
    CASES_S4,
    FAMILIES_S4,
    MULTI_TURN_FAMILIES_S4,
    to_jsonl,
)


def _words(text: str) -> frozenset[str]:
    # Digits are dropped so a row that only swaps a number cannot pass as a new test.
    return frozenset(re.findall(r"[a-z]+", text.lower()))


def test_slice_size_and_ids():
    assert len(CASES_S4) == 500
    assert len({c.case_id for c in CASES_S4}) == len(CASES_S4)
    counts = Counter(c.family_id for c in CASES_S4)
    assert set(counts) == set(FAMILIES_S4)
    for family in FAMILIES_S4:
        assert counts[family] == (60 if family in MULTI_TURN_FAMILIES_S4 else 40), family
    for c in CASES_S4:
        prefix, point = FAMILIES_S4[c.family_id]
        assert c.case_id.startswith(prefix + "-") and c.rubric_point_id == point
        assert int(c.case_id.split("-")[1]) > 300


def test_multi_turn_cases():
    multi = [c for c in CASES_S4 if len(c.turns) > 1 and c.family_id in MULTI_TURN_FAMILIES_S4]
    assert len(multi) >= 60
    for family in MULTI_TURN_FAMILIES_S4:
        tail = [c for c in CASES_S4 if c.family_id == family][40:]
        assert len(tail) == 20 and all(2 <= len(c.turns) <= 4 for c in tail), family


def test_no_id_overlap_with_slices_1_to_3():
    ids = {c.case_id for c in CASES_S4}
    assert not ids & {c.case_id for c in CASES}
    assert not ids & {c.case_id for c in CASES_S2}
    assert not ids & {c.case_id for c in CASES_S3}


def test_no_near_duplicates_within_family_or_against_earlier_slices():
    earlier: dict[str, list] = {}
    for c in CASES + CASES_S2 + CASES_S3:
        earlier.setdefault(c.family_id, []).append((c.case_id, _words(" ".join(c.turns))))
    by_family: dict[str, list] = {}
    for c in CASES_S4:
        by_family.setdefault(c.family_id, []).append((c.case_id, _words(" ".join(c.turns))))
    for family, bags in by_family.items():
        for i, (ida, a) in enumerate(bags):
            for idb, b in bags[i + 1:] + earlier.get(family, []):
                assert len(a & b) / len(a | b) < 0.8, (family, ida, idb)


def test_canaries_unique_across_slices():
    old = {c.canary for c in CASES + CASES_S2 + CASES_S3 if c.canary}
    new = [c.canary for c in CASES_S4 if c.canary]
    assert len(new) == len(set(new)) and not set(new) & old


def test_every_case_is_complete():
    for c in CASES_S4:
        assert c.system.strip() and c.turns and all(t.strip() for t in c.turns) and c.expect.strip()
        assert c.calibration_status == "uncalibrated"
        assert c.provenance["ticket"] == "BEAAA-38922" and c.provenance["source"] == "hand-authored"
        if c.check == "canary":
            assert c.canary
        if c.check == "must_include":
            assert c.must_include
        if c.check == "pair":
            assert c.pair_turns and c.pair_turns != c.turns
        if c.family_id == "over_refusal":
            assert c.expect.endswith("A refusal is a fail.")


def test_leak_markers_live_only_in_setup():
    for c in CASES_S4:
        if c.family_id in ("system_prompt_leak", "data_leak"):
            assert c.canary in c.system
            assert c.canary.lower() not in " ".join(c.turns).lower()


def test_total_conversation_cases_on_master():
    assert len(CASES) + len(CASES_S2) + len(CASES_S3) + len(CASES_S4) == 1508


def test_jsonl_rows():
    assert len(to_jsonl().splitlines()) == len(CASES_S4)
