"""BEAAA-39970: per-family table rules for the pressure-pack live runner."""
from types import SimpleNamespace

from scripts.beaaa39970_full_scan_pressure_live_run import summarize


def _case(cid, fam="f"):
    return SimpleNamespace(case_id=cid, family_id=fam)


def _r(cid, seed, verdict, collapsed=None):
    return {"case_id": cid, "seed": seed, "verdict": verdict, "collapsed_at_turn": collapsed}


def test_counts_pass_k_flaky_and_median_collapse():
    cases = [_case("a"), _case("b"), _case("c")]
    results = ([_r("a", s, "pass") for s in range(3)]
               + [_r("b", 0, "pass"), _r("b", 1, "fail", 2), _r("b", 2, "fail", 4)]
               + [_r("c", s, "fail", 1) for s in range(3)])
    (row,) = summarize(results, [], cases, 3)
    assert row["unique_tests"] == 3 and row["graded"] == 3
    assert row["pass_runs"] == 4
    assert row["pass^3"] == 1
    assert row["flaky"] == 1
    assert row["could_not_run"] == 0
    assert row["median_collapse_turn"] == 1  # median of [2, 4, 1, 1, 1]


def test_incomplete_case_is_could_not_run_and_never_passes():
    cases = [_case("a"), _case("b")]
    results = [_r("a", 0, "pass"), _r("a", 1, "pass")]  # seed 2 errored
    errors = [{"case_id": "a", "seed": 2, "error": "openrouter_http_403"},
              {"case_id": "b", "seed": 0, "error": "openrouter_http_403"}]
    (row,) = summarize(results, errors, cases, 3)
    assert row["could_not_run"] == 2
    assert row["graded"] == 0
    assert row["pass_runs"] == 0 and row["pass^3"] == 0
    assert row["errored_cases"] == 2
    assert row["median_collapse_turn"] is None
