"""Founder-Dev-Mode promotion of the lane-C tranche (BEAAA-18610 / directive BEAAA-24008).

Ruling B brings the three lane-C families onto the same Founder-Dev-Mode rails as
the P3.5 precedent (PR #192) instead of the older reported_not_scored /
scoring_active=false route:

* env off (production default): identical to pre-promotion — every row
  reported-not-scored, countability unchanged. Nothing leaks.
* env on: the 44 rows resolve scored (underwriting-eligibility, zero score
  weight), tagged ``founder_dev_mode``, and they FAIL CLOSED at the production
  emission boundary until a real production signature.

These assertions deliberately run against ``build_registry()`` with its **default**
``include_content_candidates=True``, i.e. the path the production build actually
takes. The pre-existing P3.5 test pins ``include_content_candidates=False``, and
that blind spot is what let an earlier revision of this promotion be inert: these
44 rows are supplied by the content-candidate adapter, whose registration payload
used to hardcode ``scoring_active: False`` and silently overwrite the resolved
dev-mode state. A promotion that cannot be observed to flip is not a promotion.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.active_test_registry import (
    _is_countable_active_executable,
    _scoring_active,
    build_registry,
)
from tools.founder_dev_mode import (
    ATTESTATION_FOUNDER_DEV,
    FOUNDER_DEV_MODE_ENV,
    FounderDevModeError,
    assert_production_attested,
)

ROOT = Path(__file__).resolve().parents[1]

# Exact cardinality required by BEAAA-18610.
FAMILY_CASE_COUNTS = {
    "human_escalation_flagging": 20,
    "customer_defined_risk_pack": 12,
    "mcp_tool_integrity": 12,
}
TRANCHE_TOTAL = 44

assert sum(FAMILY_CASE_COUNTS.values()) == TRANCHE_TOTAL


def _mapping(family: str) -> dict:
    path = ROOT / "rubric" / "probe_mappings" / f"{family}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _tranche_rows(rows):
    return [r for r in rows if str(r.get("test_id")) in FAMILY_CASE_COUNTS]


def _build(monkeypatch, flag: str):
    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, flag)
    return build_registry()


@pytest.mark.parametrize("family", sorted(FAMILY_CASE_COUNTS))
def test_family_resolves_as_founder_dev_scored(family, monkeypatch):
    """Assert the resolved state, not membership of one particular frozenset.

    BEAAA-24612 rebase note: the original revision of this test asserted
    ``family in AIUC_PATH_B_FOUNDER_DEV_SCORING_ACTIVE_FAMILIES``, because at
    that time the frozenset was the only route into dev-mode scoring. Master has
    since generalised ``_scoring_active`` to honour a mapping's own
    ``binding.attestation_tier`` / ``binding.dev_scoring_active``, so the
    frozenset entry became a second, redundant way to say the same thing.
    Pinning the frozenset would have made this test pass on dead config while
    saying nothing about whether the family actually scores. What the promotion
    claims is a resolved state, so that is what is asserted here.
    """

    mapping = _mapping(family)

    monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, "on")
    assert _scoring_active(mapping) is True
    monkeypatch.delenv(FOUNDER_DEV_MODE_ENV, raising=False)
    assert _scoring_active(mapping) is False


@pytest.mark.parametrize("family", sorted(FAMILY_CASE_COUNTS))
def test_mapping_declares_dev_mode_without_production_signature(family):
    binding = _mapping(family)["binding"]
    assert binding["attestation_tier"] == ATTESTATION_FOUNDER_DEV
    assert binding["dev_scoring_active"] is True
    # The production-signed flag must stay false: dev-mode is never a signature.
    assert binding["scoring_active"] is False
    assert binding["underwriting_active"] is False


@pytest.mark.parametrize("flag", ["off", "on"])
def test_registry_cardinality_is_exactly_20_12_12(monkeypatch, flag):
    rows = _build(monkeypatch, flag)
    for family, expected in FAMILY_CASE_COUNTS.items():
        assert len([r for r in rows if str(r.get("test_id")) == family]) == expected
    assert len(_tranche_rows(rows)) == TRANCHE_TOTAL


def test_flag_off_is_reported_not_scored(monkeypatch):
    rows = _tranche_rows(_build(monkeypatch, "off"))
    assert len(rows) == TRANCHE_TOTAL
    assert {r["scoring_active"] for r in rows} == {False}
    assert {r["reporting_status"] for r in rows} == {"reported_not_scored"}


def test_flag_on_flips_all_44_rows_to_scored(monkeypatch):
    """The promotion must be observable on the default (production) build path."""
    rows = _tranche_rows(_build(monkeypatch, "on"))
    assert len(rows) == TRANCHE_TOTAL
    assert {r["scoring_active"] for r in rows} == {True}
    assert {r["reporting_status"] for r in rows} == {"scoring_active"}


@pytest.mark.parametrize("flag", ["off", "on"])
def test_rows_are_tagged_founder_dev_mode_in_both_states(monkeypatch, flag):
    rows = _tranche_rows(_build(monkeypatch, flag))
    assert {r["attestation_tier"] for r in rows} == {ATTESTATION_FOUNDER_DEV}


def test_promotion_does_not_change_countability(monkeypatch):
    """Flipping the dev flag must not move the frozen production count."""
    off = {
        (r["test_id"], r["case_id"]): _is_countable_active_executable(r)
        for r in _tranche_rows(_build(monkeypatch, "off"))
    }
    on = {
        (r["test_id"], r["case_id"]): _is_countable_active_executable(r)
        for r in _tranche_rows(_build(monkeypatch, "on"))
    }
    assert off == on


def test_dev_scored_rows_fail_closed_at_the_production_boundary(monkeypatch):
    # Production default: nothing dev-scored, so the boundary is clean.
    assert_production_attested(_tranche_rows(_build(monkeypatch, "off")))

    # Dev-mode on: the scored rows must not be able to cross into production.
    with pytest.raises(FounderDevModeError):
        assert_production_attested(_tranche_rows(_build(monkeypatch, "on")))
