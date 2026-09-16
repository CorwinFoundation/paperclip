"""BEAAA-33252: the acceptance gate asserts all four terms of exit criterion 2.

Exit criterion 2 of BEAAA-21446 requires ``expected = attempted = terminal =
evidenced``.  The gate asserted three of the four, and ``expected`` silently
defaulted to ``attempted``, so on the BEAAA-33199 manifest it reduced to
``2458 == 2458 == 2458`` while 88 rows carried ``acquisition_basis: missing``
with no ``na_evidence``.  These tests are the thing that keeps that from
reappearing: a manifest whose incomplete and missing-required legs are clean
must still fail when a row asserts a result the scan never measured.
"""
import json

from api.full_scan_executor import evidenced_rows
from scripts.regrade_beaaa33199 import regrade


def _measured_row(index: int) -> dict:
    return {
        "probe_id": f"ok-{index}",
        "status": "complete",
        "verdict": "pass",
        "incomplete_reason": None,
        "target_calls": 1,
        "judge_calls": 1,
        "acquisition_basis": "target-measured",
    }


def _manifest(rows: list[dict], *, declare_expected: bool = True) -> dict:
    counts = {"evidence_rows": len(rows), "missing_required": 0}
    if declare_expected:
        counts["expected"] = len(rows)
    return {
        "evidence_rows": rows,
        "counts": counts,
        "status_counts": {"complete": len(rows)},
        "reconciliation": {
            "attempted": len(rows),
            "terminal": len(rows),
            "by_status": {"complete": len(rows)},
        },
    }


def _regrade(tmp_path, monkeypatch, manifest: dict) -> dict:
    monkeypatch.setenv("ARE_ALLOW_DEV_SIGNING_KEY", "true")
    source = tmp_path / "source.json"
    destination = tmp_path / "regraded.json"
    census = tmp_path / "census.json"
    source.write_text(json.dumps(manifest))
    census.write_text(json.dumps({"capabilities": ["tool_calling"]}))
    regrade(source, destination, census)
    return json.loads(destination.read_text())


def test_all_measured_rows_pass_the_four_way_equality(tmp_path, monkeypatch):
    manifest = _regrade(tmp_path, monkeypatch, _manifest([_measured_row(i) for i in range(4)]))

    gate = manifest["acceptance_gate"]
    assert manifest["reconciliation"]["evidenced"] == 4
    assert gate["expected_attempted_terminal_evidenced_equal"] is True
    assert gate["passed"] is True


def test_one_unjustified_missing_row_fails_the_gate(tmp_path, monkeypatch):
    """The deliverable: clean incomplete/missing-required legs, gate still fails."""
    rows = [_measured_row(i) for i in range(3)]
    rows.append(
        {
            "probe_id": "unevidenced-0",
            "status": "complete",
            "verdict": "fail",
            "incomplete_reason": None,
            "target_calls": 0,
            "judge_calls": 0,
            "acquisition_basis": "missing",
            "na_evidence": None,
        }
    )
    manifest = _regrade(tmp_path, monkeypatch, _manifest(rows))
    gate = manifest["acceptance_gate"]

    # The two legs that already existed are clean, exactly as on BEAAA-33199.
    assert gate["incomplete_false"] is True
    assert gate["zero_unapproved_missing_required"] is True
    # The three-way equality cannot see the row.  That is the defect.
    assert gate["canonical_reconciliation_equal"] is True
    # The fourth term can.
    assert manifest["reconciliation"]["attempted"] == 4
    assert manifest["reconciliation"]["terminal"] == 4
    assert manifest["reconciliation"]["evidenced"] == 3
    assert manifest["counts"]["unjustified_non_acquisition"] == 1
    assert gate["expected_attempted_terminal_evidenced_equal"] is False
    assert gate["passed"] is False


def test_justified_non_acquisition_still_counts_as_evidenced(tmp_path, monkeypatch):
    rows = [_measured_row(i) for i in range(2)]
    rows.append(
        {
            "probe_id": "na-0",
            "status": "complete",
            "verdict": "not_applicable",
            "incomplete_reason": None,
            "target_calls": 0,
            "judge_calls": 0,
            "acquisition_basis": "not-applicable",
            "na_evidence": {"capability_gate": "manifest.tool_calling=true"},
        }
    )
    manifest = _regrade(tmp_path, monkeypatch, _manifest(rows))

    assert manifest["reconciliation"]["evidenced"] == 3
    assert manifest["acceptance_gate"]["passed"] is True


def test_failed_acquisition_is_terminal_but_not_evidenced(tmp_path, monkeypatch):
    rows = [_measured_row(i) for i in range(2)]
    rows.append(
        {
            "probe_id": "failed-0",
            "status": "complete",
            "verdict": "fail",
            "incomplete_reason": None,
            "target_calls": 1,
            "judge_calls": 0,
            "acquisition_basis": "failed",
        }
    )
    manifest = _regrade(tmp_path, monkeypatch, _manifest(rows))

    assert manifest["reconciliation"]["terminal"] == 3
    assert manifest["reconciliation"]["evidenced"] == 2
    assert manifest["acceptance_gate"]["passed"] is False


def test_undeclared_expected_is_a_gate_failure_not_an_inference(tmp_path, monkeypatch):
    manifest = _regrade(
        tmp_path,
        monkeypatch,
        _manifest([_measured_row(i) for i in range(3)], declare_expected=False),
    )
    gate = manifest["acceptance_gate"]

    assert gate["expected_declared"] is False
    assert gate["canonical_reconciliation_equal"] is False
    assert gate["expected_attempted_terminal_evidenced_equal"] is False
    assert gate["passed"] is False


def test_a_row_stating_no_basis_is_evidenced_only_if_it_called_the_target():
    rows = [
        {"probe_id": "legacy-called", "status": "complete", "target_calls": 2},
        {"probe_id": "legacy-silent", "status": "complete", "target_calls": 0},
        {"probe_id": "not-terminal", "status": "incomplete", "target_calls": 5},
    ]

    assert [row["probe_id"] for row in evidenced_rows(rows)] == ["legacy-called"]
