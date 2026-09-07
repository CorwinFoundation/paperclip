"""Production adapter for registered counterfactual-stability pairs."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from harness.bench.counterfactual_pairs import judge_pair, run as run_all_pairs
from harness.runner import ModelRunner
from tools.content_candidate_inventory import materialize_candidate_case


def execute(
    subject: ModelRunner,
    judge: ModelRunner,
    case_id: str,
    registry_row: Mapping[str, Any],
) -> dict[str, Any]:
    if case_id == "cs.all_axes":
        result = run_all_pairs(
            subject,
            judge,
            Path(__file__).resolve().parents[1]
            / "rubric"
            / "probe_mappings"
            / "counterfactual_stability.yaml",
            ensemble_n=3,
        )
        trace = result["trace"]
        return {
            "payloads": [prompt for row in trace for prompt in (row["pair_a"], row["pair_b"])],
            "responses": [
                response
                for row in trace
                for response in (row["response_a"], row["response_b"])
            ],
            "judge": {
                "type": "counterfactual_stability_paired_comparator",
                "source_mode": "legacy_all_axes_rollup",
                "pooled": result["pooled"],
                "per_axis": result["per_axis"],
                "trace": trace,
            },
            "score": result["dim_raw"],
            "verdict": "pass" if result["dim_status"] == "ok" else "inconclusive",
        }

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
