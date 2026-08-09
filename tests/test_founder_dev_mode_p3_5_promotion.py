"""Founder-Dev-Mode promotion of p3_5_multiturn_jailbreak (BEAAA-WAVE1B-DRAFT).

P3.5 "Multi-turn jailbreak persistence" is promoted from reported-not-scored to
scored (underwriting-eligibility, zero score weight) under Founder-Dev-Mode:

* env off (production default): identical to pre-promotion — reported-not-scored,
  frozen active-executable count unchanged (340). Nothing leaks.
* env on: scored/countable in dev (+20 -> 360), tagged founder_dev_mode, and it
  FAILS CLOSED at the production boundary until a real production signature.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.active_test_registry import (
    AIUC_PATH_B_FOUNDER_DEV_SCORING_ACTIVE_FAMILIES,
    build_registry,
    count_active_executable,
)
from tools.founder_dev_mode import (
    ATTESTATION_FOUNDER_DEV,
    FOUNDER_DEV_MODE_ENV,
    FounderDevModeError,
    assert_production_attested,
    is_dev_mode,
)

ROOT = Path(__file__).resolve().parents[1]
FAMILY = "p3_5_multiturn_jailbreak"
MAPPING = ROOT / "rubric" / "probe_mappings" / f"{FAMILY}.yaml"

# Frozen baseline from BEAAA-16647 (include_content_candidates=False).
BASELINE_ACTIVE_EXECUTABLE = 340
P3_5_CASE_COUNT = 20


def _p3_5_rows(rows):
    return [r for r in rows if r["test_id"] == FAMILY]


def test_family_is_registered_as_founder_dev_scored():
    assert FAMILY in AIUC_PATH_B_FOUNDER_DEV_SCORING_ACTIVE_FAMILIES


def test_env_off_is_identical_to_pre_promotion(monkeypatch):
    monkeypatch.delenv(FOUNDER_DEV_MODE_ENV, raising=False)
    rows = build_registry(include_content_candidates=False)
    p35 = _p3_5_rows(rows)

    assert len(p35) == P3_5_CASE_COUNT
    assert {r["reporting_status"] for r in p35} == {"reported_not_scored"}
    assert {r["scoring_active"] for r in p35} == {False}
    # the family still declares its dev tier, it is simply not active yet
    assert {r["attestation_tier"] for r in p35} == {ATTESTATION_FOUNDER_DEV}
    # frozen customer-visible count is untouched with the flag off
    assert count_active_executable(rows) == BASELINE_ACTIVE_EXECUTABLE
    # nothing dev-scored is live, so the production boundary is clean
    assert_production_attested(rows)


def test_env_on_promotes_p3_5_to_scored(monkeypatch):
    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "on")
    assert is_dev_mode() is True
    rows = build_registry(include_content_candidates=False)
    p35 = _p3_5_rows(rows)

    assert len(p35) == P3_5_CASE_COUNT
    assert {r["reporting_status"] for r in p35} == {"scoring_active"}
    assert {r["scoring_active"] for r in p35} == {True}
    assert {r["attestation_tier"] for r in p35} == {ATTESTATION_FOUNDER_DEV}
    assert {r["activation_blocker"] for r in p35} == {None}
    # exactly P3.5's 20 cases become countable; nothing else shifts
    assert count_active_executable(rows) == BASELINE_ACTIVE_EXECUTABLE + P3_5_CASE_COUNT


def test_env_on_fails_closed_at_production_boundary(monkeypatch):
    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "on")
    rows = build_registry(include_content_candidates=False)
    with pytest.raises(FounderDevModeError) as exc:
        assert_production_attested(rows)
    assert FAMILY in str(exc.value)


def test_invalid_env_value_fails_closed(monkeypatch):
    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "maybe")
    with pytest.raises(FounderDevModeError):
        build_registry(include_content_candidates=False)


def test_zero_weight_and_no_underwriting_claim():
    data = yaml.safe_load(MAPPING.read_text(encoding="utf-8"))
    # zero score weight preserved: promotion grants eligibility, not price impact
    assert data["scoring"] == "reported_not_scored"
    assert all(pm.get("weight", 0.0) == 0.0 for pm in data.get("probes_map", []))
    # dev-mode never asserts production underwriting eligibility
    assert data["binding"]["underwriting_active"] is False
    assert data["binding"]["scoring_active"] is False  # raw production flag stays off
    assert data["binding"]["attestation_tier"] == ATTESTATION_FOUNDER_DEV


def test_production_signed_families_are_env_independent(monkeypatch):
    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "off")
    off = build_registry(include_content_candidates=False)
    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "on")
    on = build_registry(include_content_candidates=False)
    for family in ("cross_session_data_leak", "consequential_action_confirmation"):
        off_rows = [r for r in off if r["test_id"] == family]
        on_rows = [r for r in on if r["test_id"] == family]
        assert off_rows and on_rows
        assert {r["scoring_active"] for r in off_rows} == {True}
        assert {r["scoring_active"] for r in on_rows} == {True}
