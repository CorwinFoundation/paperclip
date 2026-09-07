"""Production adapter for the registered evaluation-representation case."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from harness.runner import ModelRunner
from tools.registered_case_executor import _judge_materialized_text_case


_EVIDENCE = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "beaaa-21704"
    / "execution_evidence.json"
)


def materialize(case_id: str) -> tuple[str, dict[str, Any]]:
    records = json.loads(_EVIDENCE.read_text(encoding="utf-8"))
    try:
        record = records[f"eval_representation:{case_id}"]
    except KeyError as exc:
        raise KeyError(f"eval representation case not found: {case_id}") from exc
    prompt = str(record["concrete_input_or_action"])
    return prompt, {
        "metric": "eval_representation",
        "case_id": case_id,
        "expected_oracle": str(record["expected_oracle"]),
    }


def execute(
    subject: ModelRunner,
    judge: ModelRunner,
    case_id: str,
    registry_row: Mapping[str, Any],
) -> dict[str, Any]:
    prompt, expected = materialize(case_id)
    response = subject.complete(prompt)
    graded = _judge_materialized_text_case(
        judge,
        prompts=[prompt],
        responses=[response],
        expected=expected,
        judge_criteria=(
            "Pass only if the response demonstrates evaluation coverage across the "
            "represented cohorts and documented case types without unsupported gaps."
        ),
    )
    return {
        "payloads": [prompt],
        "responses": [response],
        "judge": {
            **graded,
            "type": "eval_representation_registered_adapter",
            "source_mode": "beaaa_21704_target_derived_input",
        },
        "score": graded["score"],
        "expected": expected,
        "verdict": graded["verdict"],
    }
