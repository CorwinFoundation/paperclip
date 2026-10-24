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
  active-executable count is 512 after the additive E018 disclosure-bypass
  tranche. Dev-mode promotion does not change the flag-off count.
* Flag ON: eligible rows become scoring_active; the new E018 bypass rows remain
  report-only under existing gates. The count rises only by the families that
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

BASELINE_ACTIVE_EXECUTABLE = 512
# Non-countable-while-reported families become countable on flag-on; legacy packs
# were already counted, so they add nothing to the count (only flip to scored).
# 491 production-default + 88 earlier Founder-Dev-Mode rows + the separately
# authorized BEAAA-24008 lane-C tranche (44) = 619 on this combined train tree.
# BEAAA-35661: 632 -> 624. The construction-integrity gate refuses the 8 rows in
# CONSTRUCTION_UNREADABLE_ALIAS_FAMILIES (crescendo 4, backbond-output-attack-fixtures 4),
# whose target-channel construction is unreadable, so dev mode no longer promotes them.
# Dev-mode only: BASELINE_ACTIVE_EXECUTABLE, the flag-off production default, is unchanged,
# and no production score moves.
# E018 adds twelve already-countable disclosure cases to both flag states:
# production-default 500 -> 512 and dev-mode 624 -> 636. Activation rules stay.
EXPECTED_FLAG_ON_ACTIVE_EXECUTABLE = 636


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
        # BEAAA-35380: promoted via content-candidate ledger, not YAML benchmark path.
        # The YAML probe rows for these families are D2-blocked (no execution evidence
        # on the mapping), so their promotion is only visible with include_content_candidates=True.
        # Coverage in tests/test_beaaa35380_dev_scoring_activation.py.
        "tool_misuse_param_tamper",
        "tool_misuse_unauthorized_call",
        # BEAAA-41063: promoted only for the exact recorded-trace rows admitted
        # by its checked-in activation ledger.
        "counterfactual_stability",
    }
)
# Families within EXPECTED_PATH_B_FAMILIES that promote via the YAML benchmark path
# (scoring_active=True appears on YAML rows with include_content_candidates=False).
# Ledger-gated families rely on D2-cleared content-candidate rows instead; their
# YAML rows stay reported_not_scored on the YAML-only path.
BENCHMARK_PROMOTED_PATH_B_FAMILIES = frozenset(
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
    # Ledger-gated families must be a subset of path-B (they belong to the non-countable partition)
    assert {"tool_misuse_param_tamper", "tool_misuse_unauthorized_call"}.issubset(
        AIUC_PATH_B_FOUNDER_DEV_SCORING_ACTIVE_FAMILIES
    )


#: Families the BEAAA-35661 construction gate refuses even under dev mode: pinned
#: executable aliases whose mapping carries no prompt text and whose construction
#: is not recoverable from anywhere in the tree. Pinned exactly, so a narrowing
#: that blinded the extractor and barred additional families would fail here
#: rather than quietly shrinking the promoted set.
CONSTRUCTION_UNREADABLE_ALIAS_FAMILIES = frozenset(
    {"crescendo", "backbond-output-attack-fixtures"}
)


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
    for fam in (
        FOUNDER_DEV_LEGACY_SCORED_FAMILIES
        | BENCHMARK_PROMOTED_PATH_B_FAMILIES
    ) - CONSTRUCTION_UNREADABLE_ALIAS_FAMILIES:
        rs = by.get(fam, [])
        assert rs, fam
        if fam == "ai_disclosure":
            # E018's exact new tranche remains report-only under the existing
            # activation gates, including dev mode. Keep all twelve original
            # identity questions subject to the promotion assertions below.
            bypass_ids = {f"AD-BYP-{i:02d}" for i in range(1, 13)}
            bypass = [row for row in rs if row["case_id"] in bypass_ids]
            assert {row["case_id"] for row in bypass} == bypass_ids
            assert len(bypass) == 12
            assert {row["reporting_status"] for row in bypass} == {"reported_not_scored"}
            assert {row["scoring_active"] for row in bypass} == {False}
            rs = [row for row in rs if row["case_id"] not in bypass_ids]
            assert {row["case_id"] for row in rs} == {f"AD-{i:02d}" for i in range(1, 13)}
        assert {r["reporting_status"] for r in rs} == {"scoring_active"}, fam
        assert {r["scoring_active"] for r in rs} == {True}, fam
    # BEAAA-35661: the construction-integrity predicate bars promotion of a row
    # whose target-channel construction cannot be read, dev mode included. These
    # two families are executable aliases retained for full-scan surface only -
    # their yaml carries probe_id and taxonomy and no prompt text, and the text
    # exists nowhere else in the tree, so the predicate returns indeterminate and
    # the gate refuses them. Both packs already declare themselves
    # reporting_status: reported_not_scored / scoring_active: false and
    # "Evidence only; no score contribution", so refusing them contradicts
    # nothing; dev mode used to override that and now does not.
    for fam in CONSTRUCTION_UNREADABLE_ALIAS_FAMILIES:
        rs = by.get(fam, [])
        assert rs, fam
        assert {r["reporting_status"] for r in rs} == {"reported_not_scored"}, fam
        assert {r["scoring_active"] for r in rs} == {False}, fam
    # Ledger-gated families (BEAAA-35380) promote via content-candidate rows, not YAML
    # benchmark path. Their YAML mapping rows are D2-blocked (no execution evidence) and
    # stay reported_not_scored here; coverage is in test_beaaa35380_dev_scoring_activation.py.
    for fam in {"tool_misuse_param_tamper", "tool_misuse_unauthorized_call"}:
        rs = by.get(fam, [])
        assert rs, fam
        assert {r["reporting_status"] for r in rs} == {"reported_not_scored"}, fam
        assert {r["scoring_active"] for r in rs} == {False}, fam
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
