from __future__ import annotations

import pytest

from harness.runner import MockRunner
from tools.batch_a_local_family_target_runner import (
    FAMILIES,
    BatchALocalRunError,
    LocalExecutionPolicy,
    build_local_fixture_runners,
    run_family,
)


@pytest.mark.parametrize("family", FAMILIES)
def test_all_families_are_target_bound_and_deterministic(tmp_path, family) -> None:
    first_subject, first_judge = build_local_fixture_runners()
    second_subject, second_judge = build_local_fixture_runners()
    first = run_family(family, subject=first_subject, judge=first_judge,
                       evidence_dir=tmp_path / "a", call_cap=5000)
    second = run_family(family, subject=second_subject, judge=second_judge,
                        evidence_dir=tmp_path / "b", call_cap=5000)
    assert first["target_calls"] == second["target_calls"]
    assert first["target_calls"] > 0
    assert first["paid_calls"] == 0
    assert first["credential_class"] == "test_only"
    assert first["customer_data"] is False
    assert first["score_activation"] is False
    assert first["raw"] == second["raw"]
    assert first["evidence"]["sha256"] == second["evidence"]["sha256"]


def test_non_fixture_target_fails_closed_before_calls(tmp_path) -> None:
    calls = []
    _, judge = build_local_fixture_runners()
    with pytest.raises(BatchALocalRunError, match="test-only development fixture"):
        run_family(
            "confidence_calibration",
            subject=MockRunner(
                "configured-development-target@paid-production",
                lambda prompt: calls.append(prompt) or "answer 0.9",
            ),
            judge=judge,
            evidence_dir=tmp_path,
        )
    assert calls == []


class _PaidCapableRunner(MockRunner):
    """Spoofs the approved model id but is not the authorized fixture adapter."""


def test_paid_capable_runner_spoof_fails_closed_before_calls(tmp_path) -> None:
    calls = []
    _, judge = build_local_fixture_runners()
    subject = _PaidCapableRunner(
        "configured-development-target@fixture",
        lambda prompt: calls.append(prompt) or "answer 0.9",
    )
    with pytest.raises(BatchALocalRunError, match="test-only development fixture"):
        run_family(
            "confidence_calibration",
            subject=subject,
            judge=judge,
            evidence_dir=tmp_path,
        )
    assert calls == []


def test_exact_mockrunner_identity_spoof_fails_closed_before_calls(tmp_path) -> None:
    calls = []
    _, judge = build_local_fixture_runners()
    subject = MockRunner(
        "configured-development-target@fixture",
        lambda prompt: calls.append(prompt) or "arbitrary callback response",
    )
    with pytest.raises(BatchALocalRunError, match="test-only development fixture"):
        run_family(
            "confidence_calibration",
            subject=subject,
            judge=judge,
            evidence_dir=tmp_path,
        )
    assert calls == []


def test_unpinned_judge_fails_closed_before_calls(tmp_path) -> None:
    target_calls = []
    subject, _ = build_local_fixture_runners()
    with pytest.raises(BatchALocalRunError, match="pinned test-only fixture"):
        run_family(
            "confidence_calibration",
            subject=subject,
            judge=MockRunner("development-judge@unpinned", lambda _prompt: "{}"),
            evidence_dir=tmp_path,
        )
    assert target_calls == []


def test_caller_supplied_policy_fails_closed_before_calls(tmp_path) -> None:
    target_calls = []
    subject, judge = build_local_fixture_runners()
    with pytest.raises(BatchALocalRunError, match="unrecognized local execution policy"):
        run_family(
            "confidence_calibration",
            subject=subject,
            judge=judge,
            evidence_dir=tmp_path,
            policy=LocalExecutionPolicy(),
        )
    assert target_calls == []


def test_post_construction_callback_mutation_fails_before_replacement_call(tmp_path) -> None:
    replacement_calls = []
    target, judge = build_local_fixture_runners()
    object.__setattr__(
        target,
        "_responder",
        lambda prompt: replacement_calls.append(prompt) or "spoofed fixture response",
    )
    with pytest.raises(BatchALocalRunError, match="test-only development fixture"):
        run_family("confidence_calibration", subject=target, judge=judge,
                   evidence_dir=tmp_path)
    assert replacement_calls == []


def test_call_cap_fails_before_overrun(tmp_path) -> None:
    subject, judge = build_local_fixture_runners()
    with pytest.raises(BatchALocalRunError, match="call cap"):
        run_family(
            "confidence_calibration",
            subject=subject,
            judge=judge,
            evidence_dir=tmp_path,
            call_cap=2,
        )
