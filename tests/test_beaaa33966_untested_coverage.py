"""BEAAA-33966 TASK B: untested_coverage must count every unevidenced row.

The BEAAA-31092 rank-2 manifest carries 187 rows, 50 of which have no evidence
(``acquisition_basis: missing``), 24 of them with ``incomplete_reason: null`` as
well -- and its ``untested_coverage`` reads ``{"count": 0, "rows": []}``. That is
a false all-clear on a surface that drives underwriting. These cases pin the
fail-closed rule and would fail again if the surface went back to counting
refusals only.
"""

import json
from pathlib import Path

from api.full_scan_executor import build_untested_coverage

ROOT = Path(__file__).resolve().parents[1]
RANK2_MANIFEST = ROOT / "artifacts/beaaa-31092/rank2-run-manifest.json"


def _row(**kwargs):
    row = {
        "probe_id": "p-1",
        "test_id": "fam",
        "status": "complete",
        "verdict": "pass",
        "target_calls": 1,
        "acquisition_basis": "target-measured",
        "incomplete_reason": None,
        "na_evidence": None,
    }
    row.update(kwargs)
    return row


def test_an_unevidenced_unreasoned_row_is_visible():
    """The exact shape of the 24: no basis, no reason, and previously invisible."""
    rows = [_row(probe_id="silent", acquisition_basis="missing", target_calls=0)]
    coverage = build_untested_coverage(rows)
    assert coverage["count"] == 1
    entry = coverage["rows"][0]
    assert entry["probe_id"] == "silent"
    assert entry["category"] == "unevidenced_no_reason"


def test_an_unevidenced_row_that_states_a_reason_is_still_counted():
    rows = [
        _row(
            probe_id="stated",
            acquisition_basis="missing",
            target_calls=0,
            incomplete_reason="worker_error:w1:TimeoutError",
        )
    ]
    coverage = build_untested_coverage(rows)
    assert coverage["count"] == 1
    assert coverage["rows"][0]["category"] == "unevidenced_with_reason"


def test_refusals_keep_their_provenance():
    rows = [
        _row(
            probe_id="refused",
            verdict="edge_refusal",
            refusal={
                "refusing_party": "target",
                "reason": "policy",
                "provenance": {"source": "transcript"},
            },
        )
    ]
    coverage = build_untested_coverage(rows)
    assert coverage["rows"][0]["category"] == "edge_refusal"
    assert coverage["rows"][0]["refusing_party"] == "target"
    assert coverage["rows"][0]["provenance"] == {"source": "transcript"}


def test_evidenced_rows_do_not_appear():
    """Non-vacuity: a surface that counted everything would be as useless as one
    that counted nothing."""
    rows = [
        _row(probe_id="measured"),
        _row(probe_id="invariant", acquisition_basis="invariant-derived", target_calls=0),
        _row(
            probe_id="justified",
            acquisition_basis="not-applicable",
            verdict="not_applicable",
            target_calls=0,
            na_evidence={"profile_capability_manifest": {}, "missing_capability_name": "x"},
        ),
    ]
    assert build_untested_coverage(rows)["count"] == 0


def test_the_rank2_manifest_reports_its_remaining_twenty_four():
    """The measured case this card was filed on, after BEAAA-33970 and BEAAA-33992.

    It was 50 unevidenced rows, 24 of them unreasoned.  BEAAA-33970 re-derived
    the manifest against the eleven declarations, so the 26 capability-gated
    rows left the unevidenced population.  This guard then pinned the surviving
    24 at ``unevidenced_no_reason`` to force a ruling rather than let them be
    dispositioned quietly.  It served: the CTO ruled ``surface`` on 2026-09-18
    and authorised this change to the guard.

    What it pins now is the post-surface shape, which is strictly more specific
    than the count it replaces.  The same 24 rows must still be present and must
    each carry the ``observational_rejection`` their own evidence file records -
    a row is allowed to leave ``unevidenced_no_reason`` only by carrying that
    recorded value, never by disappearing from the population.
    """
    rows = json.loads(RANK2_MANIFEST.read_text(encoding="utf-8"))["evidence_rows"]
    surfaced = [
        row for row in rows
        if row.get("test_id") in {
            "tool_misuse_unauthorized_call", "tool_misuse_scope_escape"}
    ]
    assert len(surfaced) == 24, "the 24 must stay in the population"
    for row in surfaced:
        rejection = (row.get("na_evidence") or {}).get("observational_rejection")
        assert rejection, f"{row['probe_id']} lost its recorded observational rejection"
        assert rejection.startswith("observational_fail_closed:"), rejection
        # The verdict is the fail-closed decision itself and does not move.
        assert row["verdict"] == "fail"
        assert row["target_calls"] == 0

    coverage = build_untested_coverage(rows)
    assert coverage["count"] == 0
    assert coverage["counts_by_category"].get("unevidenced_no_reason", 0) == 0
