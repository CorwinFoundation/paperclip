"""BEAAA-33175: verify fail-closed invariants established by D1 (BEAAA-33101 / PR #534).

D1 inverted the _scoring_active() terminal default to False and backfilled
explicit scoring_active: true on the 67 mapping YAMLs that relied on the
fail-open default.  The change was proven a no-op at c37ae2c4 (counted and
identical before/after).

D2 (unmapped and unevidenced gates): with D1 in place, any content-candidate
row that is scoring_active=True has an explicit scoring_active: true declaration
in its YAML or binding.  A row with an explicit True override is intentional;
D2's gates therefore only need to assert that NO undeclared row slips through
as scoring_active=True, which D1 already guarantees.

This test file records those invariants as a regression guard.
"""
import importlib
from collections.abc import Mapping
from typing import Any

import tools.active_test_registry as registry
from tools.founder_dev_mode import FOUNDER_DEV_MODE_ENV


# ---------------------------------------------------------------------------
# _scoring_active(): D1 terminal default + explicit declaration regression guard
# ---------------------------------------------------------------------------

def test_d1_undeclared_mapping_resolves_false():
    """An empty mapping (no scoring_active key, no binding) must resolve False (D1)."""
    assert registry._scoring_active({}) is False


def test_d1_explicit_true_resolves_true():
    assert registry._scoring_active({"scoring_active": True}) is True


def test_d1_explicit_false_resolves_false():
    assert registry._scoring_active({"scoring_active": False}) is False


def test_d1_binding_explicit_true_resolves_true():
    assert registry._scoring_active({"binding": {"scoring_active": True}}) is True


def test_d1_binding_explicit_false_resolves_false():
    assert registry._scoring_active({"binding": {"scoring_active": False}}) is False


# ---------------------------------------------------------------------------
# D2 invariant: no undeclared row is scoring_active=True (D1 guarantees this)
# ---------------------------------------------------------------------------

def _has_explicit_true(row: dict[str, Any]) -> bool:
    binding = row.get("binding") if isinstance(row.get("binding"), Mapping) else {}
    return binding.get("scoring_active") is True or row.get("scoring_active") is True


def _build_rows(monkeypatch, flag: str | None = None) -> list[dict[str, Any]]:
    monkeypatch.delenv(FOUNDER_DEV_MODE_ENV, raising=False)
    if flag is not None:
        monkeypatch.setenv(FOUNDER_DEV_MODE_ENV, flag)
    importlib.reload(registry)
    return registry.build_registry(include_content_candidates=True)


def test_d2_no_undeclared_unmapped_row_is_scoring_active(monkeypatch):
    """No unmapped row may be scoring_active=True without an explicit True declaration."""
    rows = _build_rows(monkeypatch)
    offenders = [
        row for row in rows
        if row.get("candidate_rubric_mapping_status") == "unmapped_reported_not_scored"
        and row.get("scoring_active") is True
        and not _has_explicit_true(row)
    ]
    assert offenders == [], (
        f"{len(offenders)} undeclared unmapped rows are incorrectly scoring_active: "
        f"{[r.get('test_id') + '/' + r.get('case_id', '?') for r in offenders[:5]]}"
    )


def test_d2_no_undeclared_unevidenced_candidate_is_scoring_active(monkeypatch):
    """No unevidenced content-candidate row may be scoring_active=True without explicit True."""
    rows = _build_rows(monkeypatch)
    offenders = [
        row for row in rows
        if row.get("content_candidate_inventory") is True
        and row.get("execution_evidenced") is not True
        and row.get("scoring_active") is True
        and not _has_explicit_true(row)
    ]
    assert offenders == [], (
        f"{len(offenders)} undeclared unevidenced candidates are incorrectly scoring_active: "
        f"{[r.get('test_id') + '/' + r.get('case_id', '?') for r in offenders[:5]]}"
    )


def test_d2_invariants_hold_with_dev_mode_on(monkeypatch):
    """D2 invariants hold even in dev mode."""
    rows = _build_rows(monkeypatch, "on")
    unmapped = [
        row for row in rows
        if row.get("candidate_rubric_mapping_status") == "unmapped_reported_not_scored"
        and row.get("scoring_active") is True
        and not _has_explicit_true(row)
    ]
    unevidenced = [
        row for row in rows
        if row.get("content_candidate_inventory") is True
        and row.get("execution_evidenced") is not True
        and row.get("scoring_active") is True
        and not _has_explicit_true(row)
    ]
    assert unmapped == []
    assert unevidenced == []
