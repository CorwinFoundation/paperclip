"""BEAAA-39088: a failed zoo run must say why to a non-root operator seat."""

import json
import stat

from scripts import scheduled_scan


def test_summary_is_world_readable_and_count_only(tmp_path, capsys):
    run_dir = tmp_path / "2026-10-07" / "zoo"
    run_dir.mkdir(parents=True)
    run_dir.chmod(0o700)
    manifest = {
        "job": "zoo",
        "run_date": "2026-10-07",
        "aborted": False,
        "abort_reason": None,
        "reconciliation": {"routed": 3, "terminal": 2, "equal": False},
        "evidence_rows": 2,
        "total_runtime_seconds": 230.0,
        "zoo": {"targets": {"secret-ish": {"capability_manifest": "x"}}},
        "egress_preflight": {"token": "never-copied"},
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    path = scheduled_scan.publish_operator_summary(manifest_path)

    assert path == tmp_path / "2026-10-07" / "zoo.summary.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    summary = json.loads(path.read_text(encoding="utf-8"))
    assert summary["reconciliation"] == {"routed": 3, "terminal": 2, "equal": False}
    assert "zoo" not in summary and "egress_preflight" not in summary
    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert line["operator_summary"] == str(path)
    assert line["reconciliation"]["equal"] is False

def test_summary_never_changes_the_outcome_when_the_manifest_is_unreadable(tmp_path, capsys):
    """A completed scan must not fail because the summary could not be written.

    BEAAA-39088: the first cut read the manifest unguarded, which turned
    scheduled_scan.main() into a FileNotFoundError for any caller that stubs
    run_scheduled_scan (tests/test_beaaa31934_limit_per_family.py did exactly
    that). Publishing is observability; it reports and steps aside.
    """
    missing = tmp_path / "2026-10-07" / "zoo" / "manifest.json"
    returned = scheduled_scan.publish_operator_summary(missing)
    assert returned == missing
    assert not (tmp_path / "2026-10-07" / "zoo.summary.json").exists()
    skipped = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert skipped["operator_summary_skipped"] == str(missing)
    assert "error" in skipped


def test_exception_summary_is_published_and_sanitized(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKBOND_SCAN_EVIDENCE_ROOT", str(tmp_path))
    path = scheduled_scan.publish_operator_exception_summary(
        "vapi", RuntimeError("request failed Authorization: Bearer secret")
    )
    assert path is not None
    summary = json.loads(path.read_text(encoding="utf-8"))
    assert summary["status"] == "exception"
    assert summary["resumable"] is True
    assert "secret" not in summary["abort_reason"]
