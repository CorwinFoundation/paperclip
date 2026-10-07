import pytest

from scanners.form_comparability import FormRun, measure_comparability, unavailable_measurement
from scanners.variable_test_forms import FormStore, InsufficientScenarios, Scenario, Stratum


def _rows(cases):
    return tuple({"family_id": case["payload"]["family_id"],
                  "expected_weak": case["payload"]["expected_weak"],
                  "detected": case["payload"]["detected"],
                  "calibration_status": case["calibration_status"],
                  "quality_status": "accepted", "binding_status": "current"}
                 for case in cases)


def _measure_two_forms(tmp_path, product, count):
    stratum = Stratum("s1", "injection", "P1.1", "1.3.0", "medium", "high", count)
    scenarios = [Scenario(f"{product}-{i}", "s1",
                          {"prompt": f"private {product} case {i}", "family_id": "injection",
                           "expected_weak": i % 2 == 0, "detected": i % 3 != 0}, "calibrated",
                          test_spec_id=f"injection-{i % count}")
                 for i in range(count * 2)]
    store = FormStore(tmp_path / f"{product}.sqlite")
    forms = [store.create(scope=("tenant", "agent"), product=product,
                          bank_version=f"{product}/v1", strata=[stratum], scenarios=scenarios,
                          require_calibrated=True, form_id=f"{product}-form-{i}")
             for i in range(2)]
    runs = [FormRun(form["form_id"], f"{product}-run-{i}",
                    tuple(case["scenario_id"] for case in form["cases"]),
                    80.0 + i, _rows(form["cases"]), product=product)
            for i, form in enumerate(forms)]
    return forms, measure_comparability(runs)


def test_real_two_form_assembly_is_measured_separately_for_each_product(tmp_path):
    starter_forms, starter = _measure_two_forms(tmp_path, "starter", 100)
    full_forms, full = _measure_two_forms(tmp_path, "full", 2000)

    assert starter["product"] == "starter" and starter["scenario_overlap"]["max_jaccard"] == 0
    assert full["product"] == "full" and full["scenario_overlap"]["max_jaccard"] == 0
    assert [form["test_count"] for form in starter_forms] == [100, 100]
    assert [form["test_count"] for form in full_forms] == [2000, 2000]
    assert starter["new_form_score_variance"] == {
        "population_stddev": 0.5, "range": 1.0, "min": 80.0, "max": 81.0}
    assert full["new_form_score_variance"] == starter["new_form_score_variance"]


def test_ten_family_shortage_records_unavailable_instead_of_reducing_strata(tmp_path):
    strata = [Stratum(f"s{i}", f"family-{i}", f"P{i}", "1.3.0", "medium", "high", 1)
              for i in range(10)]
    scenarios = [
        Scenario(f"case-{i}-{variant}", f"s{i}", {"prompt": f"private case {i}/{variant}"},
                 "calibrated", test_spec_id=f"spec-{i}")
        for i in range(9) for variant in range(2)
    ]
    store = FormStore(tmp_path / "restricted-evidence.sqlite")
    with pytest.raises(InsufficientScenarios, match="stratum s9 needs at least 2 scenarios.*0 supplied"):
        store.create(scope=("tenant", "agent"), product="starter", bank_version="real-bank/v1",
                     strata=strata, scenarios=scenarios, require_calibrated=True)

    report = unavailable_measurement(
        "starter", required_families=(row.family_id for row in strata),
        available_families=(f"family-{i}" for i in range(9)),
    )
    assert report["status"] == "unavailable"
    assert report["missing_families"] == ["family-9"]
    assert report["scenario_overlap"] is None
