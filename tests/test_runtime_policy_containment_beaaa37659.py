"""Focused coverage for out-of-process runtime policy and quarantine evidence."""
from harness.runtime_policy_containment import (
    DEFAULT_SEEDS,
    run_runtime_policy_containment,
)


def test_declared_out_of_process_runtime_is_allowed_and_evidenced() -> None:
    result = run_runtime_policy_containment(observed_process_mode="out_of_process")

    assert result.verdict == "policy_matched"
    assert result.evidence["policy_decision"] == "allow"
    assert result.evidence["quarantine"]["applied"] is False
    assert result.evidence["action_dispatch"]["dispatched"] is True


def test_in_process_scope_deviation_is_quarantined_before_dispatch() -> None:
    result = run_runtime_policy_containment(observed_process_mode="in_process")

    assert result.verdict == "quarantined"
    assert result.evidence["policy_decision"] == "deny"
    assert result.evidence["quarantine"] == {
        "applied": True,
        "reason_code": "runtime_process_mode_mismatch",
    }
    assert result.evidence["action_dispatch"] == {
        "attempted": True,
        "dispatched": False,
    }


def test_runtime_policy_evidence_is_stable_across_n5_replay() -> None:
    first = [
        run_runtime_policy_containment(seed=seed, observed_process_mode="in_process")
        for seed in DEFAULT_SEEDS
    ]
    second = [
        run_runtime_policy_containment(seed=seed, observed_process_mode="in_process")
        for seed in DEFAULT_SEEDS
    ]

    assert [result.evidence_fingerprint for result in first] == [
        result.evidence_fingerprint for result in second
    ]
    assert all(result.to_dict()["scored"] is False for result in first)
