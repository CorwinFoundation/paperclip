"""BEAAA-31116: the controlled driver can be bounded to an immutable roster.

The rank-2 evidence for job a935915a was lost with the rest of
/var/lib/are-scanner, so those 187 rows have to be re-measured. Re-running the
whole active registry to get them would be ~2,200 cases; this pins the bounded
form and its fail-closed cases.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "beaaa29082_controlled_full_scan.py"
_spec = importlib.util.spec_from_file_location("beaaa29082_controlled_full_scan", MODULE_PATH)
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module  # dataclasses need the module resolvable
assert _spec.loader is not None
_spec.loader.exec_module(_module)

restrict_to_case_ids = _module.restrict_to_case_ids
case_id_from_registry_row = _module.case_id_from_registry_row


@pytest.fixture()
def active() -> list[dict]:
    return [
        {"test_id": "tool_misuse_scope_escape", "case_id": "TOOL_MISUSE_SE-01"},
        {"test_id": "tool_misuse_scope_escape", "case_id": "TOOL_MISUSE_SE-02"},
        {"test_id": "consequential_action_confirmation", "case_id": "cac-01"},
        {"test_id": "perturbation_seeds", "case_id": "ps.factual"},
    ]


def _roster(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "roster.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_roster_selects_only_its_rows_and_keeps_its_order(active, tmp_path: Path) -> None:
    ids = [case_id_from_registry_row(row) for row in active]
    roster = _roster(tmp_path, ids[2], ids[0])
    selected = restrict_to_case_ids(active, roster)
    assert [case_id_from_registry_row(row) for row in selected] == [ids[2], ids[0]]


def test_comments_and_blank_lines_are_ignored(active, tmp_path: Path) -> None:
    ids = [case_id_from_registry_row(row) for row in active]
    roster = _roster(tmp_path, "# BEAAA-31116 rank-2 roster", "", ids[1], "   ")
    assert len(restrict_to_case_ids(active, roster)) == 1


def test_an_unknown_case_id_fails_closed(active, tmp_path: Path) -> None:
    ids = [case_id_from_registry_row(row) for row in active]
    roster = _roster(tmp_path, ids[0], "NOT_A_REGISTERED_CASE")
    with pytest.raises(SystemExit) as excinfo:
        restrict_to_case_ids(active, roster)
    assert "NOT_A_REGISTERED_CASE" in str(excinfo.value)


def test_a_repeated_case_id_fails_closed(active, tmp_path: Path) -> None:
    ids = [case_id_from_registry_row(row) for row in active]
    roster = _roster(tmp_path, ids[0], ids[0])
    with pytest.raises(SystemExit) as excinfo:
        restrict_to_case_ids(active, roster)
    assert "repeats case ids" in str(excinfo.value)


def test_an_empty_roster_is_not_a_zero_case_run(active, tmp_path: Path) -> None:
    roster = _roster(tmp_path, "# nothing here")
    with pytest.raises(SystemExit) as excinfo:
        restrict_to_case_ids(active, roster)
    assert "no case ids" in str(excinfo.value)
