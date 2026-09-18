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


# BEAAA-33992. Every case below that patches ``build_ledger`` still has to hand
# the check a root, because "no evidence export" is now its own early finding and
# would otherwise mask what the case is actually about. The path is never read on
# those paths - the builder is patched out - so a bare tmp dir is honest here.
def _stub_root(tmp_path) -> Path:
    root = tmp_path / "evidence-export"
    root.mkdir()
    return root


def test_the_pinned_manifest_now_binds_and_an_unfit_one_still_raises():
    """The premise, restated after BEAAA-33992 moved it.

    This used to assert the pinned manifest was refused, which was the premise of
    the cases below: an unrunnable rebuild. BEAAA-33970 cleared four unfitness
    terms and BEAAA-33992 cleared the fifth, so the pinned manifest binds now and
    asserting a refusal on it would pin a defect. The fail-closed behaviour is
    asserted directly instead, on a manifest that really does disown itself.
    """
    from tools.beaaa31092_content_slice import _assert_manifest_fit_to_bind

    manifest = json.loads(drift.RUN_MANIFEST.read_text(encoding="utf-8"))
    assert _assert_manifest_fit_to_bind(manifest)["unfit_reasons"] == []

    manifest["acceptance_gate"] = {**manifest["acceptance_gate"], "passed": False}
    with pytest.raises(ValueError, match="unfit to bind"):
        _assert_manifest_fit_to_bind(manifest)


def test_a_missing_evidence_export_is_one_explicit_finding(tmp_path):
    """BEAAA-33992: the check must not mistake its own missing input for drift.

    ``recorded_target_response`` is never carried by the manifest - it is read
    from the evidence export the row names. Called without a root, every one of
    the 187 rows holds ``missing_recorded_target_response`` and the rebuild scores
    zero, which this check used to report as ~200 field differences against the
    committed ledger. That is unreadable and it is not drift. One finding now,
    and it names the flag that fixes it.
    """
    findings = drift.check_ledger_reproducible()
    assert findings == [drift.NOT_COMPARABLE], findings
    assert "--evidence-root" in drift.NOT_COMPARABLE
    assert drift.EVIDENCE_ROOT_ENV in drift.NOT_COMPARABLE


def test_an_unrunnable_rebuild_is_reported_not_swallowed(monkeypatch, tmp_path):
    """Fail-closed: a rebuild that raises is a finding, never a quiet pass."""
    def _raise(*_args, **_kwargs):
        raise ValueError("run manifest declares itself unfit to bind verdicts")

    monkeypatch.setattr(drift, "build_ledger", _raise)
    findings = drift.check_ledger_reproducible(evidence_root=_stub_root(tmp_path))
    assert findings, "the check passed while the rebuild path cannot run"
    assert any(f.startswith(drift.NOT_REPRODUCIBLE) for f in findings), findings
    raised = [f for f in findings if f.startswith(drift.NOT_REPRODUCIBLE)][0]
    assert "raised ValueError" in raised, raised


def test_the_cli_exits_nonzero_when_no_evidence_export_is_supplied(capsys, monkeypatch):
    monkeypatch.delenv(drift.EVIDENCE_ROOT_ENV, raising=False)
    assert drift.main([]) == 1
    assert drift.NOT_COMPARABLE in capsys.readouterr().out


def test_a_matching_rebuild_passes(monkeypatch, tmp_path):
    """Non-vacuity: the check is capable of green, so red means something."""
    committed = _committed()
    monkeypatch.setattr(drift, "build_ledger",
                        lambda *a, **k: {k2: v for k2, v in committed.items() if k2 != drift.STAMP_KEY})
    assert drift.check_ledger_reproducible(evidence_root=_stub_root(tmp_path)) == []


def test_a_changed_count_is_reported_as_a_field_difference(monkeypatch, tmp_path):
    committed = _committed()
    drifted = {k: v for k, v in committed.items() if k != drift.STAMP_KEY}
    drifted["scored"] = drifted["scored"] + 1
    monkeypatch.setattr(drift, "build_ledger", lambda *a, **k: drifted)
    findings = drift.check_ledger_reproducible(evidence_root=_stub_root(tmp_path))
    assert findings[0].startswith(drift.NOT_REPRODUCIBLE), findings
    assert any("scored" in f for f in findings), findings


def test_a_changed_hold_reason_is_reported_per_row(monkeypatch, tmp_path):
    committed = _committed()
    drifted = json.loads(json.dumps({k: v for k, v in committed.items() if k != drift.STAMP_KEY}))
    target = next(r for r in drifted["rows"] if r["hold_reasons"])
    target["hold_reasons"] = ["some_other_reason"]
    monkeypatch.setattr(drift, "build_ledger", lambda *a, **k: drifted)
    findings = drift.check_ledger_reproducible(evidence_root=_stub_root(tmp_path))
    assert any(f"rows[{target['stable_case_id']}]" in f for f in findings), findings


def test_the_stamp_is_absent_only_as_a_finding(monkeypatch, tmp_path):
    """A ledger quoting counts without saying when they were built is itself a finding."""
    unstamped = {k: v for k, v in _committed().items() if k != drift.STAMP_KEY}
    path = tmp_path / "unstamped.json"
    path.write_text(json.dumps(unstamped), encoding="utf-8")
    monkeypatch.setattr(drift, "build_ledger", lambda *a, **k: unstamped)
    findings = drift.check_ledger_reproducible(
        ledger_path=path, evidence_root=_stub_root(tmp_path))
    assert any(drift.STAMP_KEY in f for f in findings), findings


def test_the_committed_ledger_names_the_commit_it_was_built_at():
    """BEAAA-33992 re-authored this artifact, so the stamp moves with it.

    The stamp is produced by the build CLI now rather than added by hand, which
    is how the previous one came to describe a file the code could no longer
    reproduce. What is pinned here is the property, not the value: a full sha,
    the issue that built it, and a notice that tells a reader what the counts
    are worth.
    """
    stamp = _committed()[drift.STAMP_KEY]
    assert len(stamp["built_at_sha"]) == 40
    assert stamp["built_by_issue"] == "BEAAA-33992"
    assert "reproducible" in stamp["notice"]
    assert "evidence export" in stamp["notice"]


def test_committed_response_replay_is_advisory_and_never_flips_the_exit_code(capsys):
    """BEAAA-34038. The replay diagnostic reports; it never turns a red into a green.

    Without an export the check must still exit 1. The replay answers a different
    question - does current code still derive the committed dispositions from the
    committed responses - and says out loud that it sourced those responses from
    the artifact under test.
    """
    assert drift.main([]) == 1
    out = capsys.readouterr().out
    assert drift.NOT_COMPARABLE in out
    assert "ADVISORY (not a pass)" in out
    assert "no derived field(s) differ" in out


def test_replay_writes_an_empty_file_for_a_row_recorded_as_empty(tmp_path):
    """A row held "records no target response" replays as empty, not as absent.

    Replaying it as absent would resolve to a different hold reason and read as
    drift that is purely an artefact of the replay.
    """
    job = "job-1"
    committed = {"rows": [
        {"evidence_ref": f"evidence://{job}/fam/a.json", "recorded_target_response": "hi"},
        {"evidence_ref": f"evidence://{job}/fam/b.json", "recorded_target_response": None,
         "hold_reasons": [drift.EMPTY_RESPONSE_HOLD]},
        {"evidence_ref": f"evidence://{job}/fam/c.json", "recorded_target_response": None,
         "hold_reasons": ["missing_manifest_evidence"]},
    ]}
    assert drift._replay_export(committed, job, tmp_path) == 2
    assert json.loads((tmp_path / job / "fam/a.json").read_text())["responses"] == ["hi"]
    assert json.loads((tmp_path / job / "fam/b.json").read_text())["responses"] == []
    assert not (tmp_path / job / "fam/c.json").exists()
