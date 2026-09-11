"""Regression coverage for garak sampling-floor operator reporting."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.scheduled_scan as scheduled_scan


@pytest.mark.parametrize("target_calls", [0, 1])
def test_garak_sampling_floor_has_sampling_specific_group(target_calls: int) -> None:
    assert (
        scheduled_scan._incomplete_group(
            "garak_sample_size_below_floor:1<48", target_calls=target_calls
        )
        == "insufficient_sample"
    )


@pytest.mark.parametrize(
    ("reason", "target_calls", "expected"),
    [
        ("aborted_upstream:quota", 0, "aborted_upstream"),
        ("not_applicable", 0, "unavailable_capability"),
        ("upstream_http_403", 1, "upstream_http"),
        ("worker_failure", 1, "unavailable_capability"),
        ("connection_timeout", 1, "transport"),
        ("judge_unavailable", 1, "missing_judge"),
        ("case_execution_error:ValueError", 1, "format"),
        ("case_execution_error:ValueError", 0, "dispatch_failure"),
    ],
)
def test_existing_incomplete_groups_are_unchanged(
    reason: str, target_calls: int, expected: str
) -> None:
    assert scheduled_scan._incomplete_group(reason, target_calls=target_calls) == expected


def test_both_manifest_lanes_seed_and_count_insufficient_sample() -> None:
    row = {
        "family": "dan.AntiDAN",
        "status": "incomplete",
        "incomplete_reason": "garak_sample_size_below_floor:1<48",
        "target_calls": 1,
    }

    _, vapi_incomplete = scheduled_scan._family_summary([], [row])
    _, zoo_incomplete = scheduled_scan._zoo_family_summary([], [row])

    assert vapi_incomplete["insufficient_sample"] == 1
    assert zoo_incomplete["insufficient_sample"] == 1
    assert vapi_incomplete["format"] == 0
    assert zoo_incomplete["format"] == 0


def test_complete_measured_garak_row_is_measured_not_incomplete_in_both_lanes() -> None:
    row = {
        "family": "dan.AntiDAN",
        "status": "complete",
        "verdict": None,
        "numeric_result": 0.37,
        "hit_count": 95,
        "sample_size": 256,
        "target_calls": 256,
    }

    vapi_families, vapi_incomplete = scheduled_scan._family_summary([], [row])
    zoo_families, zoo_incomplete = scheduled_scan._zoo_family_summary([], [row])

    for families, incomplete in (
        (vapi_families, vapi_incomplete),
        (zoo_families, zoo_incomplete),
    ):
        assert families["dan.AntiDAN"]["measured"] == 1
        assert families["dan.AntiDAN"]["incomplete"] == 0
        assert sum(incomplete.values()) == 0


def test_family_summaries_always_serialize_zero_measured() -> None:
    row = {"family": "promptinject.HijackHateHumans", "status": "complete", "verdict": "PASS"}

    vapi_families, _ = scheduled_scan._family_summary([], [row])
    zoo_families, _ = scheduled_scan._zoo_family_summary([], [row])

    assert vapi_families[row["family"]]["measured"] == 0
    assert zoo_families[row["family"]]["measured"] == 0


def test_complete_measured_garak_row_is_measured_in_dimension_manifest(tmp_path: Path) -> None:
    row = {
        "family": "dan.AntiDAN",
        "rubric_point_id": "P1.1",
        "status": "complete",
        "verdict": None,
        "numeric_result": 0.37,
        "target_calls": 256,
    }
    config = SimpleNamespace(
        scanner_version="0.5.0",
        corpus_version="0.2.0",
        seed=0,
        concurrency=1,
        rpm=30,
        retries=0,
        cost_cap_usd=100,
        limit_per_family=None,
    )
    job = SimpleNamespace(
        final_manifest={"evidence_rows": [row]},
        scan_id="scan-1",
        job_id="job-1",
        status="complete",
        bind_eligibility="blocked",
        final_manifest_hash="sha256:test",
    )

    manifest = scheduled_scan._operator_manifest(
        job_name="zoo",
        config=config,
        job=job,
        preflight_path=tmp_path / "preflight.json",
        started_at="start",
        finished_at="finish",
        total_runtime_seconds=1,
        registry_rows=[],
    )

    assert manifest["dimensions"]["P1.1"] == {
        "rows": 1,
        "pass": 0,
        "fail": 0,
        "measured": 1,
        "not_applicable": 0,
        "incomplete": 0,
    }


def test_unrecognized_reason_is_visible_as_unclassified() -> None:
    assert scheduled_scan._incomplete_group("new_reason_code", target_calls=1) == "unclassified"
