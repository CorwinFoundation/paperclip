"""Target-bound reported-not-scored runner for the eight bias evidence cases."""
from __future__ import annotations

from typing import Any
from pathlib import Path

from harness.bench.counterfactual_pairs import load_pairs
from harness.eval_framework_capture import _post_json


def run_probe(probe: dict[str, Any], target: dict[str, str]) -> dict[str, str]:
    pairs = load_pairs(Path(__file__).resolve().parents[1] / "rubric" / "probe_mappings" / "counterfactual_stability.yaml")
    pair = pairs[0]
    prompts = (pair.pair_a, pair.pair_b)
    responses: list[str] = []
    for prompt in prompts:
        value = _post_json(target["url"], target["key"], {
            "model": target["model"], "temperature": 0, "seed": 0, "max_tokens": 256,
            "messages": [{"role": "user", "content": prompt}],
        })
        response = str(value["choices"][0]["message"]["content"]).strip()
        if not response:
            raise ValueError("bias target returned an empty response")
        responses.append(response)
    return {
        "input": "\n\n--- counterfactual pair ---\n\n".join(prompts),
        "oracle": "Record both counterfactual responses for downstream bias metric evaluation; do not score this registry row.",
        "response": "\n\n--- counterfactual response ---\n\n".join(responses),
        "verdict": "OBSERVED_REPORTED_NOT_SCORED",
    }
