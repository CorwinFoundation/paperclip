"""BEAAA-398: test the §2/§3/§5 auto-emission against the live manifest.

These tests exercise the three computation modules wired up by BEAAA-398:

  - harness.golden_set.calibration_delta
  - harness.golden_set.n5_band
  - harness.golden_set.discrimination

Acceptance criteria (per BEAAA-398):

  - L1 N=5 drift is 0 on every baselined fixture (release-blocker if not).
  - Calibration delta records baseline+current ARE per baselined fixture and
    flags drift attribution (rubric_reweight vs scanner_only vs none) so
    BEAAA-353 reweight is not mis-read as a scanner regression.
  - Discrimination emits spread + buckets + by-slot view; partial coverage
    surfaces in caveats, not by blocking emission.
  - Pending fixtures (G3/B2/B3/M3 as of 2026-05-16) emit pending_reason and
    do not block the others.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.golden_set.calibration_delta import compute_delta
from harness.golden_set.discrimination import compute_discrimination
from harness.golden_set.n5_band import run_l1_n5

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "artifacts" / "beaaa-387" / "manifest.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text())


@pytest.fixture(scope="module")
def baselined_ids(manifest: dict) -> set[str]:
    return {
        fid for fid, f in manifest["fixtures"].items()
        if f.get("baseline_persisted")
    }


def test_n5_l1_zero_drift_on_every_baselined_fixture(baselined_ids):
    """Supreme Law L1: pure-math re-runs must produce 0 drift."""
    band = run_l1_n5()
    baselined = {f["fixture_id"]: f for f in band["fixtures"] if f["baselined"]}
    assert set(baselined) == baselined_ids, (
        "n5_band must cover every baselined manifest fixture"
    )
    for fid, f in baselined.items():
        assert f["n_replicates"] == 5
        assert f["are_drift"] == 0, f"{fid}: L1 ARE drift {f['are_drift']} != 0"
        assert all(d == 0 for d in f["pillar_drifts"].values()), (
            f"{fid}: L1 pillar drift non-zero {f['pillar_drifts']}"
        )
        assert not f["are_breach"]
        assert not f["pillar_breaches"]
    assert band["summary"]["passed"], band["summary"]["summary_line"]
    assert band["summary"]["max_are_drift"] == 0


def test_calibration_delta_emits_per_baselined_fixture(baselined_ids):
    """§2 wiring: every baselined fixture gets baseline/current/delta."""
    delta = compute_delta()
    baselined = {f["fixture_id"]: f for f in delta["fixtures"] if f["baselined"]}
    assert set(baselined) == baselined_ids
    for fid, f in baselined.items():
        assert isinstance(f["baseline_are"], int)
        assert isinstance(f["current_are"], int)
        assert f["are_delta"] == f["current_are"] - f["baseline_are"]
        assert set(f["pillar_deltas"]) == {"OPS", "BIAS_FAIRNESS", "ROBUSTNESS", "GOVERNANCE"}
        assert f["drift_attributable_to"] in {
            "none", "rubric_reweight", "scanner_only", "unknown_regression",
        }


def test_calibration_delta_attribution_matches_baseline_versions():
    """Attribution must be rubric_reweight iff baseline rubric != current rubric."""
    delta = compute_delta()
    current_rubric = delta["current_rubric_version"]
    for f in delta["fixtures"]:
        if not f["baselined"]:
            continue
        rubric_mismatched = f["baseline_rubric_version"] != current_rubric
        if rubric_mismatched:
            assert f["drift_attributable_to"] == "rubric_reweight", (
                f"{f['fixture_id']}: baseline rubric {f['baseline_rubric_version']} != "
                f"current {current_rubric} but attribution is {f['drift_attributable_to']}"
            )
        elif f["are_delta"] == 0 and all(d == 0 for d in f["pillar_deltas"].values()):
            assert f["drift_attributable_to"] == "none"


def test_calibration_delta_supreme_law_breach_only_when_version_aligned():
    """A Supreme-Law breach is real only when versions match. rubric_reweight
    drift on cross-version baselines must NOT be marked are_breach."""
    delta = compute_delta()
    for f in delta["fixtures"]:
        if not f["baselined"]:
            continue
        if f["drift_attributable_to"] != "none":
            assert not f["are_breach"], (
                f"{f['fixture_id']}: attribution={f['drift_attributable_to']} but "
                f"are_breach=True — would mis-flag BEAAA-353 reweight as regression"
            )
            assert not f["pillar_breaches"], (
                f"{f['fixture_id']}: cross-version pillar_breaches set "
                f"{f['pillar_breaches']} — would mis-flag reweight as regression"
            )


def test_discrimination_emits_spread_buckets_and_by_slot(baselined_ids):
    disc = compute_discrimination()
    baselined = [f for f in disc["fixtures"] if f["baselined"]]
    assert {f["fixture_id"] for f in baselined} == baselined_ids
    assert disc["spread"]["current"] >= 0
    assert disc["spread"]["target"] >= 0
    # collapse_to_mean must be a clean boolean and false on current 5-fixture
    # set (spread 45, mean ~62 — slots G/M/B clearly separate).
    assert disc["distribution"]["collapse_to_mean"] is False
    by_slot = disc["distribution"]["by_slot"]
    # Slots known_good / known_bad / graded_middle must each be present.
    assert {"known_good", "known_bad", "graded_middle"}.issubset(set(by_slot))
    # No bucket should report negative or non-int counts.
    for label, count in disc["distribution"]["buckets_current"].items():
        assert isinstance(count, int)
        assert count >= 0


def test_pending_fixtures_emit_reason_and_do_not_block(manifest):
    """G3/B2/B3/M3 (as of 2026-05-16) must surface with a pending reason."""
    pending_ids = {
        fid for fid, f in manifest["fixtures"].items()
        if not f.get("baseline_persisted")
    }
    if not pending_ids:
        pytest.skip("manifest has no pending fixtures")
    for payload_fn in (compute_delta, compute_discrimination, run_l1_n5):
        payload = payload_fn()
        pending = {
            f["fixture_id"]: f for f in payload["fixtures"]
            if not f["baselined"]
        }
        assert set(pending) == pending_ids
        for fid, f in pending.items():
            assert f.get("pending_reason"), (
                f"{fid}: pending fixture must carry pending_reason"
            )


def test_artifacts_carry_caveat_metadata():
    """BEAAA-353 + partial-coverage caveats must travel with the JSON outputs."""
    delta = compute_delta()
    disc = compute_discrimination()
    band = run_l1_n5()

    # Every artifact stamps current rubric + scanner + contract version.
    for payload in (delta, disc, band):
        assert payload["current_scanner_version"]
        assert payload["current_rubric_version"]
        assert payload["current_contract_version"].startswith("are-scanner-api-v1@")
        assert payload["issue"] == "BEAAA-398"

    # Calibration delta + discrimination carry partial-coverage + 353 caveats.
    assert "beaaa353_rubric_contract_drift" in delta["caveats"]
    assert "partial_coverage" in delta["caveats"]
    assert "partial_coverage" in disc["caveats"]
    assert "beaaa353_rubric_drift" in disc["caveats"]
