from __future__ import annotations

import re
from pathlib import Path

import pytest

from harness.runner import MockRunner, RunnerError
from tools.batch_b_golden_target_runner import (
    EXPECTED_CASE_COUNTS,
    FAMILIES,
    GoldenTargetRunError,
    run_configured_development_target,
)


def _judge(good: bool) -> MockRunner:
    def complete(prompt: str) -> str:
        if "factuality judge" in prompt:
            label = "YES" if good else "NO"
            return (
                f"ANSWER_MATCH: {label}\nPLAUSIBILITY: {label}\n"
                f"FALSE_SPECIFICITY: {'NO' if good else 'YES'}\nEVIDENCE_SPAN: fixture"
            )
        if "self-consistency judge" in prompt:
            return (
                f"CONSISTENCY: {'MATCH' if good else 'CONTRADICTION'}\n"
                f"FAILURE_MODE: {'none' if good else 'fact_drift'}\n"
                "EVIDENCE_SPAN_A: fixture\nEVIDENCE_SPAN_B: fixture"
            )
        if "refusal-accuracy judge" in prompt:
            expected = re.search(r"EXPECTED_ACTION: (must_answer|must_refuse)", prompt)
            assert expected
            action = "ANSWERED" if expected.group(1) == "must_answer" else "REFUSED"
            if not good:
                action = "REFUSED" if action == "ANSWERED" else "ANSWERED"
            return f"ACTION: {action}\nFAILURE_MODE: none\nEVIDENCE_SPAN: fixture"
        if "factual-correctness judge" in prompt:
            return (
                f"CORRECTNESS: {'CORRECT' if good else 'INCORRECT'}\n"
                f"FAILURE_MODE: {'none' if good else 'wrong_fact'}\n"
                "EVIDENCE_SPAN: fixture"
            )
        raise AssertionError("unexpected judge prompt")

    return MockRunner(
        f"development-judge-{'known-good' if good else 'known-bad'}@pinned",
        complete,
    )


def _target(identity: str = "configured-development-target@fixture") -> MockRunner:
    return MockRunner(identity, lambda _prompt: "deterministic fixture answer\n0.90")


def test_known_good_executes_every_frozen_case_with_zero_paid_calls(tmp_path: Path) -> None:
    summary = run_configured_development_target(
        subject=_target(), judge=_judge(True), evidence_dir=tmp_path
    )
    assert summary["case_count"] == sum(EXPECTED_CASE_COUNTS.values()) == 90
    assert summary["target_calls"] == 110
    assert summary["judge_calls"] == 90
    assert summary["estimated_paid_call_cost_usd"] == 0.0
    assert [row["test_id"] for row in summary["families"]] == list(FAMILIES)
    assert all(row["verdict_counts"]["pass"] == row["case_count"] for row in summary["families"])


def test_known_bad_discriminates_all_families(tmp_path: Path) -> None:
    summary = run_configured_development_target(
        subject=_target("configured-development-target@known-bad"),
        judge=_judge(False),
        evidence_dir=tmp_path,
    )
    assert all(row["verdict_counts"]["fail"] == row["case_count"] for row in summary["families"])


def test_unavailable_target_fails_closed_with_per_case_evidence(tmp_path: Path) -> None:
    unavailable = MockRunner(
        "configured-development-target@unavailable",
        lambda _prompt: (_ for _ in ()).throw(RunnerError("unavailable")),
    )
    with pytest.raises(GoldenTargetRunError, match="failed closed"):
        run_configured_development_target(
            subject=unavailable, judge=_judge(True), evidence_dir=tmp_path
        )
    assert (tmp_path / "summary.json").exists()


def test_repeatability_digest_is_identical(tmp_path: Path) -> None:
    first = run_configured_development_target(
        subject=_target(), judge=_judge(True), evidence_dir=tmp_path / "one"
    )
    second = run_configured_development_target(
        subject=_target(), judge=_judge(True), evidence_dir=tmp_path / "two"
    )
    assert first["deterministic_digest"] == second["deterministic_digest"]
