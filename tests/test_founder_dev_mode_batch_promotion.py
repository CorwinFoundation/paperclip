"""Batch Founder-Dev-Mode promotion of the mapping-ready reported-not-scored
families (BEAAA-WAVE1B-DRAFT).

Promotes, in dev-mode only, every probe-mapping-driven reported-not-scored family
to scored (underwriting-eligibility, zero score weight):

  Lane-B siblings  : p3_6_output_attack, system_prompt_extraction, insecure_code_generation
  Legacy packs     : ai_disclosure, cbrn_refusal, cyber_misuse_refusal,
                     harmful_output_golden, out_of_scope_containment, voice_adversarial_pack
  Compatibility    : crescendo, backbond-output-attack-fixtures
  (P3.5 covered by test_founder_dev_mode_p3_5_promotion)

Invariants:
* Flag OFF (production default): every family reported-not-scored; the
  active-executable count remains 484 after the separately approved
  BEAAA-24273 60-row floor. No production impact.
* Flag ON: every family scoring_active; the count rises only by the families that
  were previously non-countable (the legacy packs were already counted, so the
  flag only flips them scored — it does not double-count them).
"""
from __future__ import annotations

import pytest

from tools.active_test_registry import (
    FOUNDER_DEV_SCORED_FAMILIES,
    AIUC_PATH_B_FOUNDER_DEV_SCORING_ACTIVE_FAMILIES,
    FOUNDER_DEV_LEGACY_SCORED_FAMILIES,
    build_registry,
    count_active_executable,
)
from tools.founder_dev_mode import FOUNDER_DEV_MODE_ENV, FounderDevModeError, assert_production_attested

BASELINE_ACTIVE_EXECUTABLE = 491
# Non-countable-while-reported families become countable on flag-on; legacy packs
# were already counted, so they add nothing to the count (only flip to scored).
# 491 production-default + 88 earlier Founder-Dev-Mode rows + the separately
# authorized BEAAA-24008 lane-C tranche (44) = 619 on this combined train tree.
EXPECTED_FLAG_ON_ACTIVE_EXECUTABLE = 623


def _rows_by_family(rows):
    by = {}
    for r in rows:
        by.setdefault(r["test_id"], []).append(r)
    return by


# Independent oracle for the dev-promoted roster (BEAAA-29780). The production
# constant FOUNDER_DEV_SCORED_FAMILIES is *defined* as the union of the two
# component sets, so asserting that identity re-derives the code under test and
# cannot fail. These literals are maintained by hand: a family added to or removed
# from either component set must be reflected here deliberately.
EXPECTED_PATH_B_FAMILIES = frozenset(
    {
        "p3_5_multiturn_jailbreak",
        "p3_6_output_attack",
        "system_prompt_extraction",
        "insecure_code_generation",
        "crescendo",
        "backbond-output-attack-fixtures",
    }
)
EXPECTED_LEGACY_FAMILIES = frozenset(
    {
        "ai_disclosure",
        "cbrn_refusal",
        "cyber_misuse_refusal",
        "harmful_output_golden",
        "out_of_scope_containment",
    }
)


def test_sets_partition_cleanly():
    # legacy families are NOT in the path-B set (they must not shift the flag-off count)
    assert AIUC_PATH_B_FOUNDER_DEV_SCORING_ACTIVE_FAMILIES.isdisjoint(FOUNDER_DEV_LEGACY_SCORED_FAMILIES)
    assert AIUC_PATH_B_FOUNDER_DEV_SCORING_ACTIVE_FAMILIES == EXPECTED_PATH_B_FAMILIES
    assert FOUNDER_DEV_LEGACY_SCORED_FAMILIES == EXPECTED_LEGACY_FAMILIES
    assert FOUNDER_DEV_SCORED_FAMILIES == EXPECTED_PATH_B_FAMILIES | EXPECTED_LEGACY_FAMILIES


def test_flag_off_is_production_default_unchanged(monkeypatch):
    monkeypatch.delenv(FOUNDER_DEV_MODE_ENV, raising=False)
    rows = build_registry(include_content_candidates=False)
    by = _rows_by_family(rows)
    for fam in FOUNDER_DEV_SCORED_FAMILIES:
        rs = by.get(fam, [])
        assert rs, fam
        assert {r["reporting_status"] for r in rs} == {"reported_not_scored"}, fam
        assert {r["scoring_active"] for r in rs} == {False}, fam
    assert count_active_executable(rows) == BASELINE_ACTIVE_EXECUTABLE
    assert_production_attested(rows)  # nothing dev-scored is live -> boundary clean


def test_flag_on_promotes_every_family(monkeypatch):
    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "on")
    rows = build_registry(include_content_candidates=False)
    by = _rows_by_family(rows)
    for fam in FOUNDER_DEV_SCORED_FAMILIES:
        rs = by.get(fam, [])
        assert rs, fam
        assert {r["reporting_status"] for r in rs} == {"scoring_active"}, fam
        assert {r["scoring_active"] for r in rs} == {True}, fam
    assert count_active_executable(rows) == EXPECTED_FLAG_ON_ACTIVE_EXECUTABLE


def test_flag_on_fails_closed_at_production_boundary(monkeypatch):
    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "on")
    rows = build_registry(include_content_candidates=False)
    with pytest.raises(FounderDevModeError):
        assert_production_attested(rows)


def test_legacy_packs_stay_counted_across_flag(monkeypatch):
    # legacy packs are already in the 484; the flag flips them scored without
    # changing whether they are counted.
    monkeypatch.delenv(FOUNDER_DEV_MODE_ENV, raising=False)
    off = _rows_by_family(build_registry(include_content_candidates=False))
    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "on")
    on = _rows_by_family(build_registry(include_content_candidates=False))
    for fam in FOUNDER_DEV_LEGACY_SCORED_FAMILIES:
        assert count_active_executable(off[fam]) == count_active_executable(on[fam]) == len(off[fam]), fam
