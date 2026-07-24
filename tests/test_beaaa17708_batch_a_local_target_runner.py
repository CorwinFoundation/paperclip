from __future__ import annotations

import pytest

from harness.runner import MockRunner, RunnerError
from tools.batch_a_local_family_target_runner import (
    FAMILIES,
    BatchALocalRunError,
    LocalExecutionPolicy,
    run_family,
)


def _judge() -> MockRunner:
    return MockRunner(
        "development-judge@pinned",
        lambda _prompt: (
            '{"label":"stable","routed_to_specialist":false,'
            '"urgent_flag":false,"free_consult_offered":false,'
            '"escalation_offered":false,"matches_expected":true,'
            '"warmth":0.5,"urgency":0.5,"autonomy_respect":0.5}'
        ),
    )


def _target() -> MockRunner:
    return MockRunner(
        "configured-development-target@fixture",
        lambda _prompt: "Paris Ottawa Tokyo Shakespeare 4 0.90",
    )


@pytest.mark.parametrize("family", FAMILIES)
def test_all_families_are_target_bound_and_deterministic(tmp_path, family) -> None:
    first = run_family(family, subject=_target(), judge=_judge(),
                       evidence_dir=tmp_path / "a", call_cap=5000)
    second = run_family(family, subject=_target(), judge=_judge(),
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
    with pytest.raises(BatchALocalRunError, match="test-only development fixture"):
        run_family(
            "confidence_calibration",
            subject=MockRunner(
                "configured-development-target@paid-production",
                lambda prompt: calls.append(prompt) or "answer 0.9",
            ),
            judge=_judge(),
            evidence_dir=tmp_path,
        )
    assert calls == []


class _PaidCapableRunner(MockRunner):
    """Spoofs the approved model id but is not the authorized fixture adapter."""


def test_paid_capable_runner_spoof_fails_closed_before_calls(tmp_path) -> None:
    calls = []
    subject = _PaidCapableRunner(
        "configured-development-target@fixture",
        lambda prompt: calls.append(prompt) or "answer 0.9",
    )
    with pytest.raises(BatchALocalRunError, match="test-only development fixture"):
        run_family(
            "confidence_calibration",
            subject=subject,
            judge=_judge(),
            evidence_dir=tmp_path,
        )
    assert calls == []


def test_unpinned_judge_fails_closed_before_calls(tmp_path) -> None:
    target_calls = []
    subject = MockRunner(
        "configured-development-target@fixture",
        lambda prompt: target_calls.append(prompt) or "answer 0.9",
    )
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
    with pytest.raises(BatchALocalRunError, match="unrecognized local execution policy"):
        run_family(
            "confidence_calibration",
            subject=MockRunner(
                "configured-development-target@fixture",
                lambda prompt: target_calls.append(prompt) or "answer 0.9",
            ),
            judge=_judge(),
            evidence_dir=tmp_path,
            policy=LocalExecutionPolicy(),
        )
    assert target_calls == []


def test_unavailable_target_fails_closed(tmp_path) -> None:
    target = MockRunner(
        "configured-development-target@fixture",
        lambda _prompt: (_ for _ in ()).throw(RunnerError("offline")),
    )
    with pytest.raises(BatchALocalRunError, match="unavailable"):
        run_family("confidence_calibration", subject=target, judge=_judge(),
                   evidence_dir=tmp_path)


def test_call_cap_fails_before_overrun(tmp_path) -> None:
    with pytest.raises(BatchALocalRunError, match="call cap"):
        run_family(
            "confidence_calibration",
            subject=MockRunner(
                "configured-development-target@fixture", lambda _prompt: "Paris 0.9"
            ),
            judge=_judge(),
            evidence_dir=tmp_path,
            call_cap=2,
        )
