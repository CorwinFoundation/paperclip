from scripts.ci_solution_changes import classify
from scripts.ci_select_tests import execution_shape


def test_instinct_only_does_not_activate_scanner():
    result = classify(["stage_b_pilot/mailgun.py", "ops/stage-b-inbound/install.sh"])
    assert result["instinct"] is True
    assert result["scanner"] is False


def test_scanner_only_does_not_activate_instinct():
    assert classify(["rubric/scoring.py"]) == {
        "scanner": True, "instinct": False, "reason": "classified changed paths"
    }


def test_mixed_activates_both():
    result = classify(["stage_b_pilot/mailgun.py", "rubric/scoring.py"])
    assert result["scanner"] is result["instinct"] is True


def test_shared_ci_dependency_activates_both():
    result = classify(["requirements-dev.txt"])
    assert result["scanner"] is result["instinct"] is True


def test_docs_only_activates_neither():
    result = classify(["docs/instinct.md"])
    assert result["scanner"] is result["instinct"] is False


def test_deleted_or_renamed_paths_are_classified_by_each_reported_path():
    result = classify(["stage_b_pilot/old.py", "stage_b_pilot/new.py"])
    assert result["instinct"] is True and result["scanner"] is False


def test_unavailable_selection_fails_closed():
    result = classify(None)
    assert result["scanner"] is result["instinct"] is True


def test_small_selection_uses_one_runner_and_two_workers():
    assert execution_shape("SOME", [f"tests/test_{i}.py" for i in range(9)]) == ([1], "2")


def test_tiny_selection_avoids_xdist_overhead():
    assert execution_shape("SOME", ["tests/test_one.py"]) == ([1], "1")


def test_full_or_failed_selection_preserves_six_shards():
    assert execution_shape("ALL", ["tests"]) == ([1, 2, 3, 4, 5, 6], "auto")
