"""BEAAA-39287: paid Zoo diagnostics can select exactly one registry row."""

import pytest

from scripts import scheduled_scan


def test_exact_selector_returns_only_the_named_registry_row():
    rows = [
        {"test_id": "family-a", "case_id": "case-1"},
        {"test_id": "family-a", "case_id": "case-2"},
        {"test_id": "family-b", "case_id": "case-1"},
    ]

    assert scheduled_scan._select_exact_zoo_row(rows, "family-a", "case-1") == [
        rows[0]
    ]


@pytest.mark.parametrize(
    "rows, found",
    [
        ([], 0),
        (
            [
                {"test_id": "family-a", "case_id": "case-1"},
                {"test_id": "family-a", "case_id": "case-1"},
            ],
            2,
        ),
    ],
)
def test_exact_selector_fails_closed_unless_one_row_matches(rows, found):
    with pytest.raises(RuntimeError, match=rf"found {found}$"):
        scheduled_scan._select_exact_zoo_row(rows, "family-a", "case-1")


def test_exact_zoo_selector_reaches_zoo_runner(monkeypatch, tmp_path):
    captured = {}

    def run(**kwargs):
        captured.update(kwargs)
        return tmp_path / "manifest.json"

    monkeypatch.setattr(scheduled_scan, "run_scheduled_zoo_scan", run)

    result = scheduled_scan.run_scheduled_scan(
        "zoo", zoo_family="family-a", zoo_case="case-1"
    )

    assert result == tmp_path / "manifest.json"
    assert captured["zoo_family"] == "family-a"
    assert captured["zoo_case"] == "case-1"


def test_main_requires_both_exact_zoo_selector_parts():
    try:
        scheduled_scan.main(["--job", "zoo", "--zoo-family", "family-a"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("an incomplete selector must fail argument parsing")


def test_non_zoo_job_rejects_exact_zoo_selector():
    try:
        scheduled_scan.main(
            ["--job", "vapi", "--zoo-family", "family-a", "--zoo-case", "case-1"]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Zoo selectors must not affect scheduled Vapi jobs")
