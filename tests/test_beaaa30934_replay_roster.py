"""Roster derivation for the BEAAA-30934 Vapi replay.

The first cut of the replay script subtracted a prior replay directory under
``/tmp`` and asserted an exact ``69/29/40`` triple.  The directory did not
survive, so the only reachable outcome was ``replay roster mismatch`` and the
run replayed nothing.  These tests pin the durable derivation that replaced it.
"""

from __future__ import annotations

import json
import os
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


def test_replay_refuses_without_a_current_vapi_authorization(monkeypatch):
    """BEAAA-35868: the replay unit is the one Vapi path an agent seat can start.

    Since 2026-10-06 the script carries the Founder's written authorization
    (BEAAA-30934 comment b3c74059) as a default with a fixed expiry, so the
    refusal is now exercised with an explicitly expired authorization, which the
    default never overrides.

    `install-units-only.sh` drops in a sudoers grant giving `beai-agent`
    `systemctl start` on `backbond-scan-vapi-beaaa30934.service`, so this script
    is reachable without root. It must fail closed on the same written
    authorization as the weekly lane, and it must do so before the credential is
    read - an exit code alone would not distinguish "refused" from "dispatched
    and then failed".
    """
    import importlib

    scheduled = importlib.import_module("scripts.scheduled_scan")
    replay = importlib.import_module("scripts.beaaa30934_replay_remaining")

    monkeypatch.setenv(scheduled.VAPI_AUTH_REF_ENV, "expired-test-ref")
    monkeypatch.setenv(scheduled.VAPI_AUTH_EXPIRES_ENV, "2020-01-01T00:00:00Z")

    def _credential_tripwire(*args, **kwargs):
        raise AssertionError("credential environment was read after the gate refused")

    monkeypatch.setattr(scheduled, "map_secret_environment", _credential_tripwire)

    with pytest.raises(scheduled.VapiAuthorizationError) as excinfo:
        replay.main()
    assert "expired" in str(excinfo.value)


@pytest.mark.parametrize("unset", ["both", "ref", "expires"])
def test_replay_does_not_supply_its_own_vapi_authorization(monkeypatch, unset):
    """BEAAA-38861: with the operator's authorization variables unset the
    replay refuses before reading the credential or the roster. It used to
    ``setdefault`` a ref and a self-chosen expiry, so the gate checked values
    the script had just written for itself."""
    import importlib

    scheduled = importlib.import_module("scripts.scheduled_scan")
    replay = importlib.import_module("scripts.beaaa30934_replay_remaining")

    assert not hasattr(replay, "FOUNDER_VAPI_AUTH_REF")
    assert not hasattr(replay, "FOUNDER_VAPI_AUTH_EXPIRES")
    monkeypatch.setenv(scheduled.VAPI_AUTH_REF_ENV, "operator-ref")
    monkeypatch.setenv(scheduled.VAPI_AUTH_EXPIRES_ENV, "2999-01-01T00:00:00Z")
    if unset in ("both", "ref"):
        monkeypatch.delenv(scheduled.VAPI_AUTH_REF_ENV)
    if unset in ("both", "expires"):
        monkeypatch.delenv(scheduled.VAPI_AUTH_EXPIRES_ENV)

    def _tripwire(*args, **kwargs):
        raise AssertionError("ran past the authorization gate")

    monkeypatch.setattr(scheduled, "map_secret_environment", _tripwire)
    monkeypatch.setattr(replay, "pending_case_ids", _tripwire)

    with pytest.raises(scheduled.VapiAuthorizationError) as excinfo:
        replay.main()
    assert "No VAPI traffic was sent" in str(excinfo.value)
    if unset in ("both", "ref"):
        assert scheduled.VAPI_AUTH_REF_ENV not in os.environ
    if unset in ("both", "expires"):
        assert scheduled.VAPI_AUTH_EXPIRES_ENV not in os.environ


def test_replay_summary_counts_source_unresolved_rows_before_and_after(tmp_path):
    import importlib
    import json as _json

    replay = importlib.import_module("scripts.beaaa30934_replay_remaining")
    source, out = tmp_path / "src", tmp_path / "out"
    for root, family, case, verdict in (
        (source, "mcp_tool_integrity", "c1", "unresolved"),
        (source, "mcp_tool_integrity", "c2", "unresolved"),
        (source, "mcp_tool_integrity", "c3", "pass"),
        (source, "confidence_calibration", "c4", "unresolved"),
        (out, "mcp_tool_integrity", "c1", "not_applicable"),
        (out, "mcp_tool_integrity", "c2", "pass"),
    ):
        (root / family).mkdir(parents=True, exist_ok=True)
        (root / family / f"{case}.json").write_text(_json.dumps({"verdict": verdict}))

    summary = replay.replay_summary(source, out)
    assert summary["considered"] == 3
    assert summary["still_unresolved"] == 1
    assert summary["families"]["mcp_tool_integrity"] == {
        "before": {"unresolved": 2}, "after": {"not_applicable": 1, "pass": 1}}
    assert summary["families"]["confidence_calibration"]["after"] == {"unresolved": 1}


def test_replay_reads_back_its_own_job_nested_evidence(tmp_path):
    """The replay root is <job>/<family>/<case>.json; a shallow glob missed it
    and every run re-sent the whole roster (BEAAA-30934, 2026-10-07)."""
    import importlib
    import json as _json

    replay = importlib.import_module("scripts.beaaa30934_replay_remaining")
    source, out = tmp_path / "src", tmp_path / "out"
    for path, verdict in (
        (source / "mcp_tool_integrity" / "c1.json", "unresolved"),
        (source / "mcp_tool_integrity" / "c2.json", "unresolved"),
        (out / "job-a" / "mcp_tool_integrity" / "c1.json", "pass"),
        (out / "job-b" / "mcp_tool_integrity" / "c1.json", "unresolved"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps({"verdict": verdict}))

    assert replay.resolved_case_ids(out) == {"c1"}
    assert replay.pending_case_ids(source, out) == {"c2"}
    summary = replay.replay_summary(source, out)
    assert summary["families"]["mcp_tool_integrity"]["after"] == {"pass": 1, "unresolved": 1}
    assert summary["still_unresolved"] == 1
