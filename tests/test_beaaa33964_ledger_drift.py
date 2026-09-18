"""BEAAA-33964 TASK B: the drift check must fail closed, and be green-able.

Two properties are worth a test here and they pull in opposite directions.
Fail-closed: an unrunnable rebuild is a finding, never a pass. Non-vacuous: the
check is not simply hardcoded red - given a rebuild that matches, it passes. A
check that only ever returns findings would satisfy the first and be worthless.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import beaaa33964_ledger_drift_check as drift

ROOT = Path(__file__).resolve().parents[1]


def _committed() -> dict:
    return json.loads(drift.LEDGER.read_text(encoding="utf-8"))


def test_the_rebuild_path_really_does_raise_on_the_pinned_manifest():
    """The premise. If this ever stops raising, the tests below stop being about drift."""
    from tools.beaaa31092_content_slice import build_ledger
    with pytest.raises(ValueError, match="unfit to bind"):
        build_ledger(drift.INVENTORY, drift.RUN_MANIFEST, controls=drift.CONTROLS)


def test_an_unrunnable_rebuild_is_reported_not_swallowed():
    findings = drift.check_ledger_reproducible()
    assert findings, "the check passed while the rebuild path cannot run"
    assert any(f.startswith(drift.NOT_REPRODUCIBLE) for f in findings), findings
    raised = [f for f in findings if f.startswith(drift.NOT_REPRODUCIBLE)][0]
    assert "raised ValueError" in raised, raised


def test_the_cli_exits_nonzero_while_the_rebuild_cannot_run(capsys):
    assert drift.main([]) == 1
    assert drift.NOT_REPRODUCIBLE in capsys.readouterr().out


def test_a_matching_rebuild_passes(monkeypatch):
    """Non-vacuity: the check is capable of green, so red means something."""
    committed = _committed()
    monkeypatch.setattr(drift, "build_ledger",
                        lambda *a, **k: {k2: v for k2, v in committed.items() if k2 != drift.STAMP_KEY})
    assert drift.check_ledger_reproducible() == []


def test_a_changed_count_is_reported_as_a_field_difference(monkeypatch):
    committed = _committed()
    drifted = {k: v for k, v in committed.items() if k != drift.STAMP_KEY}
    drifted["scored"] = drifted["scored"] + 1
    monkeypatch.setattr(drift, "build_ledger", lambda *a, **k: drifted)
    findings = drift.check_ledger_reproducible()
    assert findings[0].startswith(drift.NOT_REPRODUCIBLE), findings
    assert any("scored" in f for f in findings), findings


def test_a_changed_hold_reason_is_reported_per_row(monkeypatch):
    committed = _committed()
    drifted = json.loads(json.dumps({k: v for k, v in committed.items() if k != drift.STAMP_KEY}))
    target = next(r for r in drifted["rows"] if r["hold_reasons"])
    target["hold_reasons"] = ["some_other_reason"]
    monkeypatch.setattr(drift, "build_ledger", lambda *a, **k: drifted)
    findings = drift.check_ledger_reproducible()
    assert any(f"rows[{target['stable_case_id']}]" in f for f in findings), findings


def test_the_stamp_is_absent_only_as_a_finding(monkeypatch, tmp_path):
    """A ledger quoting counts without saying when they were built is itself a finding."""
    unstamped = {k: v for k, v in _committed().items() if k != drift.STAMP_KEY}
    path = tmp_path / "unstamped.json"
    path.write_text(json.dumps(unstamped), encoding="utf-8")
    monkeypatch.setattr(drift, "build_ledger", lambda *a, **k: unstamped)
    findings = drift.check_ledger_reproducible(ledger_path=path)
    assert any(drift.STAMP_KEY in f for f in findings), findings


def test_the_committed_ledger_names_the_commit_it_was_built_at():
    stamp = _committed()[drift.STAMP_KEY]
    assert stamp["built_at_sha"].startswith("f5004d43")
    assert len(stamp["built_at_sha"]) == 40
    assert "SNAPSHOT" in stamp["notice"]
    assert stamp["built_by_issue"] == "BEAAA-33611"
