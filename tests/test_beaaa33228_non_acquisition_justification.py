"""BEAAA-33228 - a row that never contacted the target must say why.

The signed full-scan manifest for job ``dd4cabb4`` (candidate
``b930e29103fc8e5a2ea7c01b672f6002513b7065``) carried 88 rows with
``acquisition_basis: missing`` and ``na_evidence: null``: 64 rendered
``not_applicable`` by a worker-side applicability gate whose predicate never
reached the manifest, and 24 rendered ``fail`` by
``observational_fail_closed:ObservationalContractError`` without a single
target call.  Every one of them is ``status: complete`` with a verdict, so
``missing_required`` and ``untested_coverage`` both read zero and the
acceptance gate never saw them.  These tests make that shape detectable.
"""

from __future__ import annotations

from api.full_scan_executor import unjustified_non_acquisition_rows


def _row(**overrides):
    row = {
        "probe_id": "case-1",
        "test_id": "tool_misuse_unauthorized_call",
        "status": "complete",
        "verdict": "fail",
        "target_calls": 0,
        "judge_calls": 0,
        "acquisition_basis": "missing",
        "na_evidence": None,
        "score_contribution": False,
        "disposition": "report-only",
    }
    row.update(overrides)
    return row


def test_missing_basis_with_null_na_evidence_is_flagged():
    """The 24 fail-verdict rows: a verdict with no acquisition and no reason."""

    rows = [_row()]

    assert unjustified_non_acquisition_rows(rows) == rows


def test_not_applicable_basis_with_null_na_evidence_is_flagged():
    """The 64 N/A rows: non-applicability asserted without stating the predicate."""

    rows = [
        _row(
            test_id="human_escalation_flagging",
            verdict="not_applicable",
            acquisition_basis="not-applicable",
        )
    ]

    assert unjustified_non_acquisition_rows(rows) == rows


def test_populated_na_evidence_clears_the_row():
    """The 272 capability-absent rows, after the BEAAA-33199/33203 fix."""

    rows = [
        _row(
            verdict="not_applicable",
            acquisition_basis="not-applicable",
            na_evidence={
                "capability_gate": "has_tool_use",
                "declared_value": False,
                "missing_capability_name": "has_tool_use",
                "profile_capability_manifest": {"has_tool_use": False},
            },
        )
    ]

    assert unjustified_non_acquisition_rows(rows) == []


def test_target_measured_rows_are_not_flagged():
    rows = [_row(acquisition_basis="target-measured", target_calls=3)]

    assert unjustified_non_acquisition_rows(rows) == []


def test_invariant_derived_rows_are_not_flagged():
    """``invariant-derived`` states its own basis; it needs no na_evidence."""

    rows = [_row(acquisition_basis="invariant-derived", verdict="pass")]

    assert unjustified_non_acquisition_rows(rows) == []


def test_incomplete_rows_are_left_to_the_missing_required_gate():
    rows = [
        _row(
            status="incomplete",
            verdict=None,
            incomplete_reason="unavailable_capability:has_tool_use=None",
        )
    ]

    assert unjustified_non_acquisition_rows(rows) == []


def test_the_full_88_row_shape_is_detected_together():
    """Both cohorts in one manifest, mixed with rows that are fine."""

    flagged = unjustified_non_acquisition_rows(
        [
            _row(test_id="tool_misuse_scope_escape"),
            _row(test_id="tool_misuse_unauthorized_call"),
            _row(
                test_id="insecure_code_generation",
                verdict="not_applicable",
                acquisition_basis="not-applicable",
            ),
            _row(
                test_id="customer_defined_risk_pack",
                verdict="not_applicable",
                acquisition_basis="not-applicable",
            ),
            _row(test_id="halueval", acquisition_basis="target-measured", target_calls=1),
        ]
    )

    assert [row["test_id"] for row in flagged] == [
        "tool_misuse_scope_escape",
        "tool_misuse_unauthorized_call",
        "insecure_code_generation",
        "customer_defined_risk_pack",
    ]


def test_observational_rejection_populates_na_evidence_in_row():
    """A fail-closed row's na_evidence carries the rejection; guard clears it."""
    rows = [
        _row(
            test_id="tool_misuse_scope_escape",
            verdict="fail",
            acquisition_basis="missing",
            na_evidence={"observational_rejection": "observational_fail_closed:ObservationalContractError"},
        )
    ]
    assert unjustified_non_acquisition_rows(rows) == []


def test_acquisition_basis_does_not_classify_observational_rejection_as_invariant_derived():
    """A fail-closed row must NOT become invariant-derived — that label asserts a
    structured invariant produced the verdict, which an exception handler did not."""
    from api.full_scan_executor import _acquisition_basis

    # Without observational_rejection: invariant-derived
    assert _acquisition_basis(
        status="complete", verdict="fail", target_calls=0, judge_calls=0
    ) == "invariant-derived"

    # With observational_rejection: stays missing (precondition failure)
    assert _acquisition_basis(
        status="complete",
        verdict="fail",
        target_calls=0,
        judge_calls=0,
        observational_rejection="observational_fail_closed:ObservationalContractError",
    ) == "missing"
