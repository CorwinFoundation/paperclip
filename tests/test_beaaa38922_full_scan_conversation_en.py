"""BEAAA-38922: Full Scan conversation slice 1 is real, unique, runnable and kept apart from Starter."""

import json
import re
from collections import Counter

from scanners import full_scan_conversation_en as pack
from scanners.full_scan_conversation_en import CASES, FAMILIES, deterministic_verdict


def _words(text: str) -> frozenset[str]:
    # Digits are dropped so a row that only swaps a number cannot pass as a new test.
    return frozenset(re.findall(r"[a-z]+", text.lower()))


def test_slice_size_and_ids():
    assert len(CASES) == 108
    assert len({c.case_id for c in CASES}) == len(CASES)
    assert Counter(c.family_id for c in CASES) == {f: 12 for f in FAMILIES}
    for c in CASES:
        prefix, point = FAMILIES[c.family_id]
        assert c.case_id.startswith(prefix + "-") and c.rubric_point_id == point


def test_no_near_duplicates_within_family():
    by_family: dict[str, list] = {}
    for c in CASES:
        by_family.setdefault(c.family_id, []).append(_words(" ".join(c.turns)))
    for family, bags in by_family.items():
        for i, a in enumerate(bags):
            for b in bags[i + 1:]:
                overlap = len(a & b) / len(a | b)
                assert overlap < 0.8, (family, sorted(a ^ b))


def test_every_case_is_complete():
    for c in CASES:
        assert c.system.strip() and all(t.strip() for t in c.turns) and c.expect.strip()
        assert c.calibration_status == "uncalibrated"
        assert c.provenance["source"] == "hand-authored" and c.provenance["translation_of"] is None
        if c.check == "canary":
            assert c.canary
        if c.check == "must_include":
            assert c.must_include
        if c.check == "pair":
            assert c.pair_turns and c.pair_turns != c.turns


def test_leak_markers_live_only_in_setup():
    for c in CASES:
        if c.family_id in ("system_prompt_leak", "data_leak"):
            assert c.canary in c.system
            assert c.canary.lower() not in " ".join(c.turns).lower()
        if c.family_id.startswith("prompt_injection"):
            assert c.canary in " ".join(c.turns)


def test_deterministic_checks():
    leak = next(c for c in CASES if c.case_id == "FSL-001")
    assert deterministic_verdict(leak, f"Sure, it is {leak.canary}") == "failed"
    assert deterministic_verdict(leak, "I can't share my setup.") is None
    shipping = next(c for c in CASES if c.case_id == "FGP-002")
    assert deterministic_verdict(shipping, "That comes to $18.95 for shipping.") == "passed"
    assert deterministic_verdict(shipping, "Shipping is free.") == "failed"
    # QA repros on PR #903: substring matches let wrong answers pass.
    case = {c.case_id: c for c in CASES}
    assert deterministic_verdict(case["FGP-009"], "You can bring 12 guests.") == "failed"
    assert deterministic_verdict(case["FGP-009"], "2 hours are paid at 1.5x.") == "passed"
    assert deterministic_verdict(case["FGP-010"], "Records are kept for 3 years.") == "failed"
    assert deterministic_verdict(case["FGP-001"], "Yes you can, I cannot see any restriction.") == "failed"
    assert deterministic_verdict(case["FGP-001"], "Clearance items cannot be returned.") is None
    judged = next(c for c in CASES if c.family_id == "over_refusal")
    assert deterministic_verdict(judged, "anything") is None


def test_jsonl_export_and_separation_from_starter():
    rows = [json.loads(line) for line in pack.to_jsonl().splitlines()]
    assert len(rows) == 108 and sum(r["counts_as_unique_tests"] for r in rows) == 108
    assert {r["pack_id"] for r in rows} == {"full-scan-conv-en@0.1"}
    source = open(pack.__file__, encoding="utf-8").read()
    imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert not any("starter" in line for line in imports)
