"""BEAAA-31934: --limit-per-family makes the reduced zoo slice expressible.

The 118-row acceptance slice of BEAAA-31326 is "one case per routed family".
Before this flag the only lever was ``BACKBOND_SCAN_COST_CAP_USD``, which caps
spend and aborts partway through, so it buys partial family coverage instead of
one case everywhere. These tests pin both halves: the slice is exactly one row
per routed family, and the unlimited path the scheduled units run is untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.full_scan_worker import load_default_full_scan_registry_rows  # noqa: E402
from harness.controlled_zoo import load_surface_census, route_registry_rows  # noqa: E402
from scripts import scheduled_scan  # noqa: E402


@pytest.fixture(scope="module")
def zoo_decisions():
    rows = load_default_full_scan_registry_rows()
    return route_registry_rows(rows, load_surface_census())


def _config(**overrides) -> scheduled_scan.ScheduleConfig:
    base = dict(
        tenant_id="t",
        agent_endpoint="https://example.invalid/",
        evidence_root=Path("/tmp/beaaa31934"),
        scanner_version="1.0.0",
        corpus_version="1.0.0",
        seed=0,
        concurrency=1,
        rpm=30,
        retries=0,
        cost_cap_usd=1.0,
        timeout_seconds=60,
        judge_model="judge",
        execution_environment="test",
    )
    base.update(overrides)
    return scheduled_scan.ScheduleConfig(**base)


def test_limit_one_yields_exactly_one_case_per_routed_family(zoo_decisions):
    limited = scheduled_scan._limit_decisions_per_family(zoo_decisions, 1)
    routed = [d for d in limited if d.target is not None]
    assert all(len(d.rows) == 1 for d in routed)
    # The slice size BEAAA-31326 accepts: one case for each routed family.
    assert sum(len(d.rows) for d in routed) == len(routed)
    assert [d.family for d in limited] == [d.family for d in zoo_decisions]


def test_limit_leaves_unrouted_families_whole(zoo_decisions):
    limited = scheduled_scan._limit_decisions_per_family(zoo_decisions, 1)
    before = {d.family: d for d in zoo_decisions if d.target is None}
    after = {d.family: d for d in limited if d.target is None}
    assert before.keys() == after.keys()
    # Unrouted rows never dispatch; they land as reason-coded incompletes, so
    # truncating them would understate the unrouted surface in the manifest.
    for family, decision in before.items():
        assert after[family].rows == decision.rows


def test_limit_above_the_largest_family_is_a_no_op(zoo_decisions):
    widest = max(len(d.rows) for d in zoo_decisions)
    limited = scheduled_scan._limit_decisions_per_family(zoo_decisions, widest)
    assert [d.rows for d in limited] == [d.rows for d in zoo_decisions]


def test_unlimited_decisions_are_unchanged(zoo_decisions):
    assert scheduled_scan._limit_decisions_per_family(zoo_decisions, None) == list(
        zoo_decisions
    )


def test_row_limit_keeps_one_row_per_family_in_registry_order():
    rows = list(load_default_full_scan_registry_rows())
    limited = scheduled_scan._limit_rows_per_family(rows, 1)
    families = [scheduled_scan._family_name(raw) for raw in limited]
    assert len(families) == len(set(families))
    assert set(families) == {scheduled_scan._family_name(raw) for raw in rows}
    # It keeps the first registry row of each family, not an arbitrary one.
    first_seen: dict[str, object] = {}
    for raw in rows:
        first_seen.setdefault(scheduled_scan._family_name(raw), raw)
    assert limited == list(first_seen.values())


def test_row_limit_none_is_the_same_list():
    rows = list(load_default_full_scan_registry_rows())
    assert scheduled_scan._limit_rows_per_family(rows, None) == rows


def test_manifest_records_the_limit():
    job = SimpleNamespace(
        final_manifest={"evidence_rows": [], "budget": {}},
        scan_id="scan",
        job_id="job",
        status="complete",
        bind_eligibility="eligible",
        final_manifest_hash="hash",
    )
    common = dict(
        job_name="vapi",
        job=job,
        preflight_path=Path("/tmp/beaaa31934/pre.json"),
        started_at="2026-09-11T00:00:00+00:00",
        finished_at="2026-09-11T00:01:00+00:00",
        total_runtime_seconds=60.0,
        registry_rows=[],
    )
    sliced = scheduled_scan._operator_manifest(config=_config(limit_per_family=1), **common)
    full = scheduled_scan._operator_manifest(config=_config(), **common)
    assert sliced["limit_per_family"] == 1
    assert full["limit_per_family"] is None


def test_default_config_is_unlimited():
    assert _config().limit_per_family is None


def test_zero_and_negative_limits_are_rejected():
    for bad in (0, -1):
        with pytest.raises(RuntimeError, match="limit-per-family"):
            _config(limit_per_family=bad).validate()


def test_cli_passes_the_limit_through(monkeypatch):
    seen: dict[str, object] = {}

    def fake_run(job, *, limit_per_family=None):
        seen["job"] = job
        seen["limit"] = limit_per_family
        return Path("/tmp/beaaa31934/manifest.json")

    monkeypatch.setattr(scheduled_scan, "run_scheduled_scan", fake_run)
    assert scheduled_scan.main(["--job", "vapi", "--limit-per-family", "1"]) == 0
    assert seen == {"job": "vapi", "limit": 1}

    seen.clear()
    assert scheduled_scan.main(["--job", "vapi"]) == 0
    assert seen == {"job": "vapi", "limit": None}


def test_cli_rejects_a_zero_limit():
    with pytest.raises(SystemExit):
        scheduled_scan.main(["--job", "vapi", "--limit-per-family", "0"])
