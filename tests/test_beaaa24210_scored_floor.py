"""BEAAA-24210 deterministic scored-case floor acceptance."""
from collections import Counter
from pathlib import Path

import pytest

from harness.bench import scored_lane_common as bench
from tools.active_test_registry import build_registry, validate_registry

FAMILIES = {
    "p1_2_citation_integrity_floor": ("P1.2", 4),
    "p1_6_eval_breadth_floor": ("P1.6", 7),
    "p2_5_sentiment_skew_floor": ("P2.5", 7),
    "p3_2_indirect_injection_floor": ("P3.2", 7),
    "p3_3_secret_exfil_floor": ("P3.3", 10),
}
BASELINE = {"P1.2": 16, "P1.6": 13, "P2.5": 13, "P3.2": 13, "P3.3": 10}
EXPECTED_ADDITIONS = {"P1.2": 4, "P1.6": 7, "P2.5": 7, "P3.2": 7, "P3.3": 10}
ROOT = Path(__file__).resolve().parents[1]


def test_strict_registry_and_floor_counts():
    rows = build_registry()
    assert validate_registry(rows, 0, strict=True) == []
    selected = [r for r in rows if r["test_id"] in FAMILIES]
    assert len(selected) == 35
    assert all(r["scoring_active"] is True for r in selected)
    assert all(r["reporting_status"] == "scored" for r in selected)
    assert all(r["execution_evidenced"] is True for r in selected)
    assert all(r["target_bound"] is True for r in selected)
    assert all(r["canonical_runtime_case"] is True for r in selected)
    assert all(r["alias_of_runtime_case_identity"] is None for r in selected)
    counts = Counter(
        str(r["risk_or_rubric_id"])
        for r in rows
        if r.get("canonical_runtime_case") and r.get("reporting_status") == "scored"
    )
    for point, before in BASELINE.items():
        assert counts[point] >= 20
        assert counts[point] - before == EXPECTED_ADDITIONS[point]


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_fixtures_oracles_evidence_and_n5(family):
    point, expected = FAMILIES[family]
    probes = bench.load_probes(bench.family_yaml_path(family))
    assert len(probes) == expected
    assert len({p.payload_sha256() for p in probes}) == expected
    assert all(p.provenance_source and p.provenance_pinned_at == "2026-08-18" for p in probes)
    good_path = ROOT / "tests" / "fixtures" / family / "known_good_v0.json"
    bad_path = ROOT / "tests" / "fixtures" / family / "known_bad_v0.json"
    good = bench.run(family_id=family, fixture_path=good_path)
    bad = bench.run(family_id=family, fixture_path=bad_path)
    assert good["n_pass"] == expected and good["n_fail"] == 0
    assert bad["n_fail"] == expected and bad["n_pass"] == 0
    assert all(row["evidence_uri"].startswith("yaml://") for row in good["case_rows"])
    assert len({bench.envelope_fingerprint(bench.run(family_id=family, fixture_path=bad_path)) for _ in range(5)}) == 1
