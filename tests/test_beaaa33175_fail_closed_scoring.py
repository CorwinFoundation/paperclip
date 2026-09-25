"""BEAAA-33175: scoring_active must fail closed.

D1 - an undeclared ``scoring_active`` resolves False, not True.
D2 - a row may not be scoring-active while its candidate rubric mapping is
     ``unmapped_reported_not_scored`` or its execution is unevidenced.
"""

import pytest

from tools import construction_integrity_gate
from tools.active_test_registry import (
    _scoring_active,
    apply_fail_closed_scoring,
    build_registry,
    row_blocks_scoring_activation,
)

# BEAAA-35661 added construction integrity as a third fail-closed predicate, so
# "mapped and evidenced" is no longer on its own enough to carry weight - a row
# must also have had its target-channel construction read and found clean.
#
# The D2 rows below are invented, not corpus rows, so they have no construction
# to audit and the new predicate bars them. That is the gate behaving correctly;
# it is pinned directly by test_a_row_that_was_never_audited_is_refused in
# tests/test_beaaa35661_construction_promotion_gate.py. Declaring these rows
# audited-clear here keeps the D2 tests measuring D2 - mapping and evidence -
# instead of silently re-measuring the construction gate.
#
# This fixture grants nothing to the gate's own suite and nothing to the live
# registry: it is scoped to this module and keyed to these invented case ids.
@pytest.fixture(autouse=True)
def _synthetic_rows_are_audited_clear(monkeypatch):
    real = construction_integrity_gate.load_dispositions

    def _with_synthetic(*args, **kwargs):
        table = dict(real(*args, **kwargs))
        for case_id in ("c",):
            table.setdefault(case_id, construction_integrity_gate.CLEAR)
        return table

    monkeypatch.setattr(
        construction_integrity_gate, "load_dispositions", _with_synthetic
    )

# D1 landed on master (BEAAA-33101 terminal `return False`); the hold is over.

def test_d1_no_declaration_resolves_false():
    assert _scoring_active({"benchmark": "undeclared_family"}) is False


def test_d1_explicit_declarations_still_win():
    assert _scoring_active({"benchmark": "f", "scoring_active": True}) is True
    assert _scoring_active({"benchmark": "f", "scoring_active": False}) is False
    assert _scoring_active({"benchmark": "f", "binding": {"scoring_active": True}}) is True
    assert _scoring_active({"benchmark": "f", "binding": {"scoring_active": False}}) is False


def test_d2_unmapped_row_cannot_be_scoring_active():
    row = {
        "test_id": "f",
        "case_id": "c",
        "scoring_active": True,
        "reporting_status": "scored",
        "execution_evidenced": True,
        "candidate_rubric_mapping_status": "unmapped_reported_not_scored",
    }
    assert row_blocks_scoring_activation(row) is True
    apply_fail_closed_scoring([row])
    assert row["scoring_active"] is False
    assert row["reporting_status"] == "reported_not_scored"


def test_d2_unevidenced_row_cannot_be_scoring_active():
    row = {
        "test_id": "f",
        "case_id": "c",
        "scoring_active": True,
        "reporting_status": "scored",
        "execution_evidenced": False,
    }
    assert row_blocks_scoring_activation(row) is True
    apply_fail_closed_scoring([row])
    assert row["scoring_active"] is False
    assert row["reporting_status"] == "reported_not_scored"


def test_d2_mapped_and_evidenced_row_survives():
    row = {
        "test_id": "f",
        "case_id": "c",
        "scoring_active": True,
        "reporting_status": "scored",
        "execution_evidenced": True,
    }
    assert row_blocks_scoring_activation(row) is False
    apply_fail_closed_scoring([row])
    assert row["scoring_active"] is True
    assert row["reporting_status"] == "scored"


def test_live_registry_has_no_unmapped_or_unevidenced_scoring_active_row():
    offenders = [
        (row.get("test_id"), row.get("case_id"))
        for row in build_registry()
        if row.get("scoring_active") is True and row_blocks_scoring_activation(row)
    ]
    assert offenders == []
