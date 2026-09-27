"""BEAAA-24504: both tool-misuse behavioral predicates move a golden score."""
from __future__ import annotations

import copy
import json

from rubric.scoring import Rubric, score
from scripts import beaaa14423_n5_calibration as golden

POINTS = (
    "ROBUSTNESS.TOOL_MISUSE.UNAUTHORIZED_CALL",
    "ROBUSTNESS.TOOL_MISUSE.PARAM_TAMPER",
)


def _score(eval_outputs: dict) -> dict:
    root = golden.FIXTURES / "B1"
    expected = json.loads((root / "expected_score.json").read_text())
    return score(
        Rubric.load(golden.RUBRIC_PATH), eval_outputs,
        snapshot_hash=golden.SNAPSHOT_HASH,
        forensic_bundle_uri="https://golden.local/B1",
        scored_at=golden.SCORED_AT,
        corpus_version=expected.get("corpus_version"), seed=expected.get("seed"),
        model_versions=expected.get("model_versions"),
    )


def test_each_tool_misuse_fail_moves_the_integer_golden_score():
    root = golden.FIXTURES / "B1"
    eval_outputs = json.loads((root / "eval_outputs.json").read_text())
    baseline = _score(eval_outputs)
    assert (baseline["bmi"], baseline["band"]["display"]) == (37, "D")
    for point in POINTS:
        activated = copy.deepcopy(eval_outputs)
        activated[point]["raw"] = 0.0
        candidate = _score(activated)
        assert candidate["bmi"] == 36, point
        assert candidate["bmi"] < baseline["bmi"], point
