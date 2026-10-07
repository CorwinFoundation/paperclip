import pytest

from scanners.form_comparability import (
    FormRun,
    InvalidComparabilitySample,
    measure_comparability,
    unavailable_measurement,
)


def _run(form, run, ids, score, outcomes, replay=None, product="starter"):
    rows = tuple(
        {"family_id": family, "expected_weak": weak, "detected": detected,
         "calibration_status": "calibrated", "quality_status": "accepted", "binding_status": "current"}
        for family, weak, detected in outcomes
    )
    return FormRun(form, run, tuple(ids), score, rows, replay, product)


def test_separates_new_forms_from_exact_replay_and_reports_uncertainty():
    a = _run("f1", "r1", ["a", "b"], 80, [("inj", True, True), ("inj", False, False)])
    b = _run("f2", "r2", ["c", "d"], 84, [("inj", True, False), ("inj", False, True)])
    replay = _run("f1", "r3", ["a", "b"], 81, [("inj", True, True)], "r1")
    report = measure_comparability([a, b, replay])
    assert report["status"] == "measured"
    assert report["product"] == "starter"
    assert report["scenario_overlap"] == {"pair_count": 1, "mean_jaccard": 0.0, "max_jaccard": 0.0}
    assert report["new_form_score_variance"]["population_stddev"] == 2.0
    assert report["new_form_score_variance"]["range"] == 4
    assert report["seed_replay_variance"] == {"pair_count": 1, "max_absolute_delta": 1}
    assert report["families"]["inj"]["detection"]["rate"] == 0.5
    assert report["families"]["inj"]["false_alarm"]["rate"] == 0.5
    assert report["families"]["inj"]["detection"]["lower_95"] < 0.5


@pytest.mark.parametrize("field,value", [("calibration_status", "pending"), ("quality_status", "degraded"),
                                           ("answer_leakage", True), ("binding_status", "stale")])
def test_rejects_unsafe_cases(field, value):
    good = _run("f1", "r1", ["a"], 80, [("inj", True, True)])
    bad_row = dict(good.results[0]); bad_row[field] = value
    bad = FormRun("f2", "r2", ("b",), 82, (bad_row,))
    with pytest.raises(InvalidComparabilitySample):
        measure_comparability([good, bad])


def test_replay_must_preserve_private_form_identity():
    a = _run("f1", "r1", ["a"], 80, [("inj", True, True)])
    b = _run("f2", "r2", ["b"], 82, [("inj", True, True)])
    replay = _run("f1", "r3", ["different"], 80, [("inj", True, True)], "r1")
    with pytest.raises(InvalidComparabilitySample, match="replay does not match"):
        measure_comparability([a, b, replay])


def test_starter_and_full_scan_are_never_combined():
    starter = _run("f1", "r1", ["a"], 80, [("inj", True, True)])
    full = _run("f2", "r2", ["b"], 82, [("inj", True, True)], product="full")
    with pytest.raises(InvalidComparabilitySample, match="measured separately"):
        measure_comparability([starter, full])


def test_missing_family_supply_is_recorded_without_fabricated_metrics():
    report = unavailable_measurement(
        "starter", required_families=(f"family-{i}" for i in range(10)),
        available_families=(f"family-{i}" for i in range(7)),
    )
    assert report == {
        "status": "unavailable", "product": "starter",
        "reason": "insufficient_scenario_supply", "required_family_count": 10,
        "available_family_count": 7,
        "missing_families": ["family-7", "family-8", "family-9"],
        "scenario_overlap": None, "new_form_score_variance": None,
        "seed_replay_variance": None, "families": None,
    }
