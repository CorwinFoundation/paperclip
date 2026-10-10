"""BEAAA-39970: full-scan-pressure-en@0.1 pack integrity checks."""

import json
import re
from collections import Counter

from scanners import full_scan_pressure_en as pack
from scanners.full_scan_pressure_en import CASES, FAMILIES, PACK_ID, to_jsonl


def _words(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z]+", text.lower()))


def test_case_count():
    assert len(CASES) == 300


def test_unique_ids():
    ids = [c.case_id for c in CASES]
    assert len(ids) == len(set(ids))


def test_family_distribution():
    dist = Counter(c.family_id for c in CASES)
    assert dist == {f: 30 for f in FAMILIES}


def test_id_prefixes_and_rubric_points():
    for c in CASES:
        prefix, point = FAMILIES[c.family_id]
        assert c.case_id.startswith(prefix + "-"), c.case_id
        assert c.rubric_point_id == point, c.case_id


def test_every_case_complete():
    for c in CASES:
        assert c.system.strip(), c.case_id
        assert 2 <= len(c.turns) <= 5, (c.case_id, len(c.turns))
        assert all(t.strip() for t in c.turns), c.case_id
        assert c.expect == "refuse", c.case_id
        assert c.check == "judge", c.case_id
        assert c.pass_rule.strip(), c.case_id
        assert c.tactic.strip(), c.case_id
        assert c.calibration_status == "uncalibrated"
        assert c.provenance["source"] == "hand-authored"


def test_action_families_have_sandbox_end_state():
    action_families = {"action_pressure", "scope_escalation"}
    for c in CASES:
        if c.family_id in action_families:
            assert c.sandbox_end_state is not None, c.case_id
            assert c.sandbox_end_state.get("expected_no_action") is True, c.case_id
        else:
            assert c.sandbox_end_state is None, c.case_id


def test_no_near_duplicates_within_family():
    by_family: dict[str, list] = {}
    for c in CASES:
        by_family.setdefault(c.family_id, []).append(_words(" ".join(c.turns)))
    for family, bags in by_family.items():
        for i, a in enumerate(bags):
            for b in bags[i + 1 :]:
                overlap = len(a & b) / len(a | b) if (a | b) else 0
                assert overlap < 0.8, (family, sorted(a ^ b)[:5])


def test_jsonl_export():
    rows = [json.loads(line) for line in to_jsonl().splitlines()]
    assert len(rows) == 300
    assert {r["pack_id"] for r in rows} == {PACK_ID}
    assert sum(r["counts_as_unique_tests"] for r in rows) == 300
    for r in rows:
        assert "tactic" in r
        assert "pass_rule" in r
        assert "sandbox_end_state" in r
        assert isinstance(r["turns"], list)


def test_source_is_self_contained():
    source = open(pack.__file__, encoding="utf-8").read()
    assert "full-scan-pressure-en@0.1" in source
    assert "BEAAA-39970" in source
