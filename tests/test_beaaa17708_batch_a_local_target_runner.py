from __future__ import annotations

import pytest

from harness.runner import MockRunner, RunnerError
from tools.batch_a_local_family_target_runner import BatchALocalRunError, run_family


def _judge() -> MockRunner:
    return MockRunner("development-judge@pinned", lambda _prompt: "{}")


def test_confidence_calibration_is_target_bound_and_deterministic(tmp_path) -> None:
    target = MockRunner(
        "configured-development-target@fixture",
        lambda _prompt: "Paris Ottawa Tokyo Shakespeare 4 0.90",
    )
    first = run_family("confidence_calibration", subject=target, judge=_judge(),
                       evidence_dir=tmp_path / "a")
    second = run_family("confidence_calibration", subject=target, judge=_judge(),
                        evidence_dir=tmp_path / "b")
    assert first["target_calls"] == second["target_calls"] == 100
    assert first["paid_calls"] == 0
    assert first["customer_data"] is False
    assert first["score_activation"] is False
    assert first["raw"] == second["raw"]
    assert first["evidence"]["sha256"] == second["evidence"]["sha256"]


def test_non_configured_target_fails_closed(tmp_path) -> None:
    with pytest.raises(BatchALocalRunError, match="configured development target"):
        run_family(
            "confidence_calibration",
            subject=MockRunner("generic", lambda _prompt: "answer 0.9"),
            judge=_judge(),
            evidence_dir=tmp_path,
        )


def test_unavailable_target_fails_closed(tmp_path) -> None:
    target = MockRunner(
        "configured-development-target@unavailable",
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
