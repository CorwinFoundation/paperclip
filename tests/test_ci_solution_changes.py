from scripts.ci_solution_changes import classify
from scripts.ci_select_tests import execution_shape


def test_instinct_only_does_not_activate_scanner():
    result = classify(["stage_b_pilot/mailgun.py", "ops/stage-b-inbound/install.sh"])
    assert result["instinct"] is True
    assert result["scanner"] is False


def test_observed_instinct_merge_does_not_activate_scanner():
    # Exact file set from master commit 6cfeb86 (PR #838). This merge exposed
    # that an issue-numbered Instinct test was incorrectly treated as scanner.
    result = classify([
        "stage_b_pilot/history_import.py",
        "stage_b_pilot/instinct_risk_checks.py",
        "stage_b_pilot/instinct_standing.py",
        "stage_b_pilot/mailgun.py",
        "tests/test_beaaa37962_instinct_standing.py",
        "tests/test_beaaa38060_instinct_risk_checks.py",
    ])
    assert result["instinct"] is True
    assert result["scanner"] is False


def test_all_current_instinct_test_families_do_not_activate_scanner():
    paths = [
        "tests/test_capture_instinct_acceptance.py",
        "tests/test_beaaa38116_stage_b_v1_acceptance.py",
        "tests/test_beaaa38120_muse_intake.py",
        "tests/test_beaaa38147_reusable_onboarding.py",
        "tests/test_beaaa38279_instinct_stop_relay.py",
        "tests/test_beaaa38728_stage_b_status.py",
        "tests/test_beaaa38775_stage_b_detail.py",
        "tests/test_beaaa38867_stage_b_v2.py",
    ]
    result = classify(paths)
    assert result["instinct"] is True
    assert result["scanner"] is False


def test_neighboring_issue_numbered_scanner_tests_stay_in_scanner_lane():
    result = classify([
        "tests/test_beaaa37931_tenant_account_boundary_trace.py",
        "tests/test_beaaa37986_code_agent_supply_chain_trace.py",
    ])
    assert result["scanner"] is True
    assert result["instinct"] is False


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


def test_dot_prefixed_workflow_path_activates_both():
    result = classify([".github/workflows/tests.yml"])
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


def test_empty_selection_fails_closed():
    result = classify([])
    assert result["scanner"] is result["instinct"] is True


def test_small_selection_uses_one_runner_and_two_workers():
    assert execution_shape("SOME", [f"tests/test_{i}.py" for i in range(9)]) == ([1], "2")


def test_tiny_selection_avoids_xdist_overhead():
    assert execution_shape("SOME", ["tests/test_one.py"]) == ([1], "1")


def test_full_or_failed_selection_preserves_six_shards():
    assert execution_shape("ALL", ["tests"]) == ([1, 2, 3, 4, 5, 6], "auto")
