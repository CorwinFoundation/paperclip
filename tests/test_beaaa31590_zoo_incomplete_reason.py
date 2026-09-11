"""A provider rejection must not be reported as malformed target output.

The 2026-09-10 nightly zoo run recorded 2,234 rows under ``format``.  Every one
of them had actually died on an OpenRouter HTTP 403 credit cap, with no
``incomplete_reason`` set and a bare ``unresolved`` verdict.  Grouping those as
``format`` points an operator at the targets when the fault was upstream.
"""

from __future__ import annotations

import scripts.scheduled_scan as scheduled_scan


def _judge_403_row() -> dict[str, object]:
    """Shape taken verbatim from a retained 2026-09-10 zoo evidence row."""
    return {
        "family": "browser_surface_action_integrity",
        "test_id": "browser_surface_action_integrity",
        "target_calls": 1,
        "judge_calls": 0,
        "verdict": "unresolved",
        "error": "case_execution_error:RunnerError",
        "error_detail": (
            'openrouter HTTP 403: {"error":{"message":"Key limit exceeded '
            '(total limit).","code":403}}'
        ),
    }


def test_provider_rejection_groups_as_transport() -> None:
    row = _judge_403_row()
    group = scheduled_scan._incomplete_group(
        scheduled_scan._incomplete_reason_value(row),
        target_calls=int(row["target_calls"]),
    )
    assert group == "transport"


def test_explicit_reason_still_wins_over_error_detail() -> None:
    row = _judge_403_row()
    row["incomplete_reason"] = "judge_unavailable"
    assert scheduled_scan._incomplete_reason_value(row) == "judge_unavailable"
    assert (
        scheduled_scan._incomplete_group(
            scheduled_scan._incomplete_reason_value(row), target_calls=1
        )
        == "missing_judge"
    )


def test_zoo_family_summary_reports_transport_not_format() -> None:
    _, incomplete = scheduled_scan._zoo_family_summary([], [_judge_403_row()])
    assert incomplete["transport"] == 1
    assert incomplete["format"] == 0
