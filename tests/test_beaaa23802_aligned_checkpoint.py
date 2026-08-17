from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from harness.golden_set.aligned_checkpoint import DEFAULT_CHECKPOINT, compare_aligned_checkpoint

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = ROOT / "artifacts" / "beaaa-387"


def candidate() -> dict:
    checkpoint = json.loads(DEFAULT_CHECKPOINT.read_text())
    return {
        "scanner_version": checkpoint["scanner_version"],
        "rubric_version": checkpoint["rubric_version"],
        "scoring_semantics": checkpoint["scoring_semantics"],
        "fixtures": copy.deepcopy(checkpoint["fixtures"]),
    }


def test_masked_fixtures_cannot_pass():
    payload = candidate()
    for row in payload["fixtures"].values():
        row["status"] = "masked"
    result = compare_aligned_checkpoint(payload)
    assert result["status"] == "BREACH"
    assert {failure["code"] for failure in result["failures"]} == {"MASKED_FIXTURE"}


def test_aligned_two_point_robustness_drift_blocks():
    payload = candidate()
    payload["fixtures"]["M3"]["pillars"]["ROBUSTNESS"] += 2
    result = compare_aligned_checkpoint(payload)
    assert result["status"] == "BREACH"
    assert result["failures"][0]["code"] == "ALIGNED_DRIFT_BREACH"


def test_historical_baselines_remain_byte_identical():
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in HISTORICAL.glob("*/baseline.json")}
    assert compare_aligned_checkpoint(candidate())["status"] == "PASS"
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in HISTORICAL.glob("*/baseline.json")}
    assert after == before


def test_missing_pins_fail_closed_and_b1_empty_corpus_pin_is_valid():
    payload = candidate()
    assert payload["fixtures"]["B1"]["corpus_version"] == ""
    del payload["fixtures"]["B1"]["corpus_version"]
    result = compare_aligned_checkpoint(payload)
    assert result["status"] == "BREACH"
    assert result["failures"][0] == {"fixture_id": "B1", "code": "MISSING_REQUIRED_PINS", "pins": ["corpus_version"]}


def test_no_approved_aligned_checkpoint_blocks(tmp_path: Path):
    result = compare_aligned_checkpoint(candidate(), checkpoint_path=tmp_path / "missing.json")
    assert result["status"] == "BLOCKED_NO_ALIGNED_BASELINE"
