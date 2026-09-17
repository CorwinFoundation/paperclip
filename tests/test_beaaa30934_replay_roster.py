"""Roster derivation for the BEAAA-30934 Vapi replay.

The first cut of the replay script subtracted a prior replay directory under
``/tmp`` and asserted an exact ``69/29/40`` triple.  The directory did not
survive, so the only reachable outcome was ``replay roster mismatch`` and the
run replayed nothing.  These tests pin the durable derivation that replaced it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import beaaa30934_replay_remaining as replay


def _record(root: Path, family: str, case_id: str, verdict: str | None) -> None:
    (root / family).mkdir(parents=True, exist_ok=True)
    body: dict[str, object] = {"case_id": case_id}
    if verdict is not None:
        body["verdict"] = verdict
    (root / family / f"{case_id}.json").write_text(json.dumps(body))


def test_unresolved_ids_are_read_case_insensitively(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _record(source, "fam_a", "case-1", "unresolved")
    _record(source, "fam_a", "case-2", "UNRESOLVED")
    _record(source, "fam_b", "case-3", "pass")
    _record(source, "fam_b", "case-4", None)

    assert replay.unresolved_case_ids(source) == {"case-1", "case-2"}


def test_malformed_evidence_does_not_abort_the_roster(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _record(source, "fam_a", "case-1", "unresolved")
    (source / "fam_a" / "case-broken.json").write_text("{not json")

    assert replay.unresolved_case_ids(source) == {"case-1"}


def test_replay_skips_rows_it_already_resolved(tmp_path: Path) -> None:
    source = tmp_path / "source"
    replayed = tmp_path / "replay" / "evidence"
    for case_id in ("case-1", "case-2", "case-3"):
        _record(source, "fam_a", case_id, "unresolved")
    _record(replayed, "fam_a", "case-1", "pass")
    _record(replayed, "fam_a", "case-2", "unresolved")

    assert replay.pending_case_ids(source, replayed) == {"case-2", "case-3"}


def test_absent_replay_root_is_a_first_run_not_a_failure(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _record(source, "fam_a", "case-1", "unresolved")

    assert replay.pending_case_ids(source, tmp_path / "never-written") == {"case-1"}


def test_missing_source_root_names_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "2026-09-08" / "vapi" / "evidence" / "absent"

    with pytest.raises(FileNotFoundError, match=str(missing)):
        replay.pending_case_ids(missing, tmp_path / "replay")


def test_select_rows_reports_pending_ids_with_no_registry_row() -> None:
    rows = [{"case_id": "case-1"}]

    with pytest.raises(RuntimeError, match="no registry row"):
        replay.select_rows(rows, {"case-1", "case-2"})


def test_select_rows_refuses_a_roster_over_the_call_budget() -> None:
    rows = [{"case_id": f"case-{index}"} for index in range(replay.MAX_TARGET_CALLS + 1)]

    with pytest.raises(RuntimeError, match="exceeds the .* replay budget"):
        replay.select_rows(rows, {str(row["case_id"]) for row in rows})


def test_select_rows_returns_registry_rows_for_the_roster() -> None:
    rows = [{"case_id": "case-1"}, {"case_id": "case-2"}, {"case_id": "case-3"}]

    assert replay.select_rows(rows, {"case-1", "case-3"}) == [{"case_id": "case-1"}, {"case_id": "case-3"}]
