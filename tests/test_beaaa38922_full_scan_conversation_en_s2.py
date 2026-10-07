"""BEAAA-38922: Full Scan conversation slice 2 is real, unique, runnable and apart from slice 1."""

import re
from collections import Counter

from scanners.full_scan_conversation_en import CASES, FAMILIES
from scanners.full_scan_conversation_en_s2 import CASES_S2, FAMILIES_S2, to_jsonl


def _words(text: str) -> frozenset[str]:
    # Digits are dropped so a row that only swaps a number cannot pass as a new test.
    return frozenset(re.findall(r"[a-z]+", text.lower()))


def test_slice_size_and_ids():
    assert len(CASES_S2) >= 400
    assert len({c.case_id for c in CASES_S2}) == len(CASES_S2)
    counts = Counter(c.family_id for c in CASES_S2)
    assert all(counts[f] == 40 for f in FAMILIES)
    assert counts["tool_misuse"] == 20 and counts["hallucinated_action"] == 20
    for c in CASES_S2:
        prefix, point = FAMILIES_S2[c.family_id]
        assert c.case_id.startswith(prefix + "-") and c.rubric_point_id == point


def test_no_near_duplicates_within_family():
    by_family: dict[str, list] = {}
    for c in CASES_S2:
        by_family.setdefault(c.family_id, []).append((c.case_id, _words(" ".join(c.turns))))
    for family, bags in by_family.items():
        for i, (ida, a) in enumerate(bags):
            for idb, b in bags[i + 1:]:
                assert len(a & b) / len(a | b) < 0.8, (family, ida, idb)


def test_every_case_is_complete():
    for c in CASES_S2:
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
    for c in CASES_S2:
        if c.family_id in ("system_prompt_leak", "data_leak"):
            assert c.canary in c.system
            assert c.canary.lower() not in " ".join(c.turns).lower()


def test_no_id_overlap_with_slice1():
    assert not {c.case_id for c in CASES_S2} & {c.case_id for c in CASES}


def test_jsonl_rows():
    assert len(to_jsonl().splitlines()) == len(CASES_S2)
