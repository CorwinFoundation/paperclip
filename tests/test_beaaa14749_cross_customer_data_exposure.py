from harness.bench.cross_customer_data_exposure import run


def test_known_good_is_raw_four_and_non_scoring():
    result = run(fixture_path="tests/fixtures/r2_rob/cross_customer_data_exposure/known_good_v0.json")
    assert result["raw"] == 4.0
    assert result["scoring_active"] is False


def test_known_bad_is_raw_zero_and_non_scoring():
    result = run(fixture_path="tests/fixtures/r2_rob/cross_customer_data_exposure/known_bad_v0.json")
    assert result["raw"] == 0.0
    assert result["scoring_active"] is False
