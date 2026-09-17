"""BEAAA-33175: scoring_active must fail closed.

D1 - an undeclared ``scoring_active`` resolves False, not True.
D2 - a row may not be scoring-active while its candidate rubric mapping is
     ``unmapped_reported_not_scored`` or its execution is unevidenced.
"""

import pytest

from tools.active_test_registry import (
    _scoring_active,
    apply_fail_closed_scoring,
    build_registry,
    row_blocks_scoring_activation,
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
