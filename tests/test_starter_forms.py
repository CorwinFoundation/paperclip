"""starter-100@4 forms through the shared assembler (BEAAA-38986)."""

from collections import Counter

import pytest

from scanners.starter_forms import cases_from_form, reserve_starter_cases, reserve_starter_form
from scanners.starter_pack_v4 import (
    STARTER_V4_BANK,
    STARTER_V4_UPSTREAM_PENDING,
    case_from_payload,
    starter_v4_scenarios,
    starter_v4_strata,
)
from scanners.variable_test_forms import FormStore, InsufficientScenarios, Scenario, Stratum

_SOURCE = {p["case_id"]: p for _s, p, _c in STARTER_V4_BANK}


def _ready_bank():
    strata = [Stratum(**s) for s in starter_v4_strata() if s["family_id"] not in STARTER_V4_UPSTREAM_PENDING]
    ids = {s.stratum_id for s in strata}
    scenarios = [Scenario(**s) for s in starter_v4_scenarios() if s["stratum_id"] in ids]
    return strata, scenarios


def test_first_form_is_ten_by_ten_and_grades_from_its_own_payload(tmp_path):
    store = FormStore(tmp_path / "forms.sqlite")
    cases, summary = reserve_starter_cases(scan_id="scan-1", tenant_id="t1", agent_id="a1", store=store)
    assert len(cases) == 100 and summary["test_count"] == 100
    assert set(Counter(c.family_id for c in cases).values()) == {10}
    for case in cases:
        assert case == case_from_payload(_SOURCE[case.case_id])
    assert "cases" not in summary and "selection_seed" not in summary


def test_retry_of_same_scan_replays_the_saved_form(tmp_path):
    store = FormStore(tmp_path / "forms.sqlite")
    first = reserve_starter_form(store, scan_id="scan-1", tenant_id="t1", agent_id="a1")
    again = reserve_starter_form(store, scan_id="scan-1", tenant_id="t1", agent_id="a1")
    assert again == first
    assert cases_from_form(again) == cases_from_form(first)


def test_consecutive_runs_draw_disjoint_scenarios_with_no_fixed_core(tmp_path):
    store = FormStore(tmp_path / "forms.sqlite")
    strata, scenarios = _ready_bank()
    forms = [
        store.create(scope=("t1", "a1"), product="starter", bank_version="test", strata=strata,
                     scenarios=scenarios, history_window=1, form_id=f"scan-{n}")
        for n in range(6)
    ]
    picked = [{c["scenario_id"] for c in f["cases"]} for f in forms]
    for prev, cur in zip(picked, picked[1:]):
        assert not prev & cur
        assert len(cur) == 100
    assert not set.intersection(*picked)


def test_previous_run_excluded_with_spare_supply_for_new_forms(tmp_path):
    store = FormStore(tmp_path / "forms.sqlite")
    forms = [reserve_starter_form(store, scan_id=f"scan-{n}", tenant_id="t1", agent_id="a1") for n in range(3)]
    hashes = [{c["input_hash"] for c in form["cases"]} for form in forms]
    assert all(not a & b for a, b in zip(hashes, hashes[1:]))
    assert all(form["policy"]["history_window"] == 1 for form in forms)
    # A different agent has no history, so its first run still assembles.
    assert len(reserve_starter_form(store, scan_id="another-agent", tenant_id="t1", agent_id="a2")["cases"]) == 100


def test_retry_reads_saved_payloads_even_when_current_bank_is_unavailable(tmp_path, monkeypatch):
    from scanners import starter_forms
    store = FormStore(tmp_path / "forms.sqlite")
    first = reserve_starter_form(store, scan_id="scan", tenant_id="t", agent_id="a")
    monkeypatch.setattr(starter_forms, "starter_v4_scenarios", lambda: ())
    assert reserve_starter_form(store, scan_id="scan", tenant_id="t", agent_id="a") == first
