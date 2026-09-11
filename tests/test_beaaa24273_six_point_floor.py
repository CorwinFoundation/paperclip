"""BEAAA-24273 six-point 30-case-floor acceptance.

Contract: contracts/are-scanner-api-v1.md@v1.3.0.
Classification: rule-execution under scanner-implementation-supervision-v1@v1.1.
"""
from collections import Counter
from pathlib import Path

import pytest

from harness.bench import scored_lane_common as bench
from tools.active_test_registry import build_registry, validate_registry


FAMILIES = {
    "p1_2_citation_integrity_floor30": "P1.2",
    "p1_6_eval_breadth_floor30": "P1.6",
    "p2_5_sentiment_skew_floor30": "P2.5",
    "p3_2_indirect_injection_floor30": "P3.2",
    "p3_3_secret_exfil_floor30": "P3.3",
    "p1_5_confidence_calibration_floor30": "P1.5",
}
ROOT = Path(__file__).resolve().parents[1]


def test_strict_registry_has_no_missing_contract_rows_or_runtime_aliases():
    rows = build_registry()
    assert validate_registry(rows, 0, strict=True) == []
    selected = [row for row in rows if row["test_id"] in FAMILIES]
    assert len(selected) == 60
    assert all(row["scoring_active"] is True for row in selected)
    assert all(row["reporting_status"] == "scored" for row in selected)
    assert all(row["execution_evidenced"] is True for row in selected)
    assert all(row["target_bound"] is True for row in selected)
    assert all(row["canonical_runtime_case"] is True for row in selected)
    assert all(row["alias_of_runtime_case_identity"] is None for row in selected)
    counts = Counter(
        str(row["risk_or_rubric_id"])
        for row in rows
        if row.get("canonical_runtime_case") and row.get("reporting_status") == "scored"
    )
    assert {point: counts[point] for point in FAMILIES.values()} == {
        "P1.2": 30, "P1.6": 30, "P2.5": 30,
        "P3.2": 30, "P3.3": 31, "P1.5": 31,
    }


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_explicit_oracles_negative_fixtures_evidence_and_n5(family):
    probes = bench.load_probes(bench.family_yaml_path(family))
    assert len(probes) == 10
    mapping = bench.load_mapping(bench.family_yaml_path(family))
    assert mapping["version"] == "0.2.0"
    assert mapping["corpus_version"] == bench.FAMILY_CORPUS_VERSIONS[family]
    assert len({probe.payload_sha256() for probe in probes}) == 10
    assert len({probe.oracle_sha256() for probe in probes}) == 10
    assert all(probe.provenance_source for probe in probes)
    assert all(probe.provenance_pinned_at == "2026-08-18" for probe in probes)
    assert all("Floor-30 case" not in probe.prompt for probe in probes)
    assert all(
        "compliant" not in marker.casefold()
        for probe in probes
        for marker in (*probe.pass_requires_any, *probe.fail_on_any)
    )
    good = bench.run(
        family_id=family,
        fixture_path=ROOT / "tests" / "fixtures" / family / "known_good_v0.json",
    )
    bad_path = ROOT / "tests" / "fixtures" / family / "known_bad_v0.json"
    bad = bench.run(family_id=family, fixture_path=bad_path)
    assert (good["n_pass"], good["n_fail"]) == (10, 0)
    assert (bad["n_pass"], bad["n_fail"]) == (0, 10)
    assert all(row["evidence_uri"].startswith("yaml://") for row in good["case_rows"])
    assert len({bench.envelope_fingerprint(bench.run(family_id=family, fixture_path=bad_path)) for _ in range(5)}) == 1
