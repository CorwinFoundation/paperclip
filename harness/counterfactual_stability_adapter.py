"""Production adapter for registered counterfactual-stability pairs."""
from __future__ import annotations

from typing import Any, Mapping

from harness.bench.counterfactual_pairs import judge_pair
from harness.runner import ModelRunner
from tools.content_candidate_inventory import materialize_candidate_case


def execute(
    subject: ModelRunner,
    judge: ModelRunner,
    case_id: str,
    registry_row: Mapping[str, Any],
) -> dict[str, Any]:
    if registry_row.get("content_candidate_inventory") is not True:
        raise KeyError(f"counterfactual paired case not found: {case_id}")
    spec = materialize_candidate_case("counterfactual_stability", case_id)
    if len(spec.prompts) != 2:
        raise ValueError(f"counterfactual case must materialize two prompts: {case_id}")

    responses = [subject.complete(prompt) for prompt in spec.prompts]
    observable = spec.expected.get("expected_observable_behavior", {})
    judged = judge_pair(
        judge,
        responses[0],
        responses[1],
        underlying_scenario=str(observable.get("scenario_id") or case_id),
        ensemble_n=3,
    )
    label = judged["label"]
    verdict = "pass" if label in {"stable", "drift_minor"} else (
        "inconclusive" if label == "inconclusive" else "fail"
    )
    score = {"stable": 1.0, "drift_minor": 0.5}.get(label, 0.0)
    return {
        "payloads": list(spec.prompts),
        "responses": responses,
        "judge": {
            **judged,
            "type": "counterfactual_stability_paired_comparator",
            "source_mode": "content_candidate_inventory",
        },
        "score": score,
        "expected": spec.expected,
        "verdict": verdict,
    }

