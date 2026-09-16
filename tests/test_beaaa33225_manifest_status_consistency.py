"""BEAAA-33225: a manifest may not sign summaries that contradict its rows."""
import json

import pytest

from api.full_scan_executor import (
    FullScanExecutorError,
    manifest_status_consistency_errors,
    seal_manifest,
)
from scripts.regrade_beaaa33199 import regrade


def _source_manifest(incomplete_rows: int, complete_rows: int) -> dict:
    rows = [
        {
            "probe_id": f"cap-{index}",
            "status": "incomplete",
            "incomplete_reason": "unavailable_capability:tool_calling=None",
        }
        for index in range(incomplete_rows)
    ]
    rows += [
        {"probe_id": f"ok-{index}", "status": "complete", "incomplete_reason": None}
        for index in range(complete_rows)
    ]
    total = len(rows)
    by_status = {
        status: count
        for status, count in (
            ("complete", complete_rows),
            ("incomplete", incomplete_rows),
        )
        if count
    }
    return {
        "evidence_rows": rows,
        "counts": {
            "expected": total,
            "evidence_rows": total,
            "missing_required": incomplete_rows,
        },
        "status_counts": dict(by_status),
        "reconciliation": {
            "attempted": total,
            "terminal": total,
            "by_status": dict(by_status),
        },
    }


def test_regrade_recomputes_reconciliation_from_the_regraded_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("ARE_ALLOW_DEV_SIGNING_KEY", "true")
    source = tmp_path / "source.json"
    destination = tmp_path / "regraded.json"
    census = tmp_path / "census.json"
    source.write_text(json.dumps(_source_manifest(3, 5)))
    census.write_text(json.dumps({"capabilities": ["tool_calling"]}))

    assert regrade(source, destination, census) == (3, 0)
    manifest = json.loads(destination.read_text())

    assert manifest["reconciliation"]["by_status"] == {"complete": 8}
    assert manifest["reconciliation"]["attempted"] == 8
    assert manifest["reconciliation"]["terminal"] == 8
    assert manifest["status_counts"] == {"complete": 8}
    assert manifest["acceptance_gate"]["canonical_reconciliation_equal"] is True
    assert manifest["acceptance_gate"]["passed"] is True
    assert manifest_status_consistency_errors(manifest) == []


def test_stale_by_status_fails_to_seal(monkeypatch):
    monkeypatch.setenv("ARE_ALLOW_DEV_SIGNING_KEY", "true")
    stale = _source_manifest(3, 5)
    for row in stale["evidence_rows"]:
        row["status"] = "complete"
        row["incomplete_reason"] = None
    stale["status_counts"] = {"complete": 8}

    errors = manifest_status_consistency_errors(stale)
    assert len(errors) == 1
    assert "reconciliation.by_status" in errors[0]
    with pytest.raises(FullScanExecutorError, match="not self-consistent"):
        seal_manifest(stale)


def test_stale_attempted_and_terminal_fail_to_seal(monkeypatch):
    monkeypatch.setenv("ARE_ALLOW_DEV_SIGNING_KEY", "true")
    stale = _source_manifest(0, 4)
    stale["reconciliation"]["attempted"] = 9
    stale["reconciliation"]["terminal"] = 9

    errors = manifest_status_consistency_errors(stale)
    assert len(errors) == 2
    with pytest.raises(FullScanExecutorError, match="not self-consistent"):
        seal_manifest(stale)


def test_consistent_manifest_still_seals(monkeypatch):
    monkeypatch.setenv("ARE_ALLOW_DEV_SIGNING_KEY", "true")
    manifest = _source_manifest(2, 6)

    assert manifest_status_consistency_errors(manifest) == []
    assert seal_manifest(manifest)["manifest_hash"].startswith("sha256:")


def test_manifest_without_summaries_is_not_checked():
    assert manifest_status_consistency_errors({"acceptance_gate": {"passed": True}}) == []
