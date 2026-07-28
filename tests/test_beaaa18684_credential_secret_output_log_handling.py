import json
from pathlib import Path
import yaml
from evals.aiuc_path_b.credential_secret_output_log_handling import AIUC_SOURCE, CASES, CORPUS_VERSION, RUBRIC_POINT_IDS, corpus_sha256, evaluate, run
ROOT = Path(__file__).resolve().parents[1]

def test_a008_pin_and_twelve_safe_case_classes():
    assert AIUC_SOURCE.endswith("d407401393de5629745de6accb35e000e674043f#A008")
    assert CORPUS_VERSION == "credential_secret_output_log_handling@0.1.0"
    assert len(CASES) == len({c["case_id"] for c in CASES}) == 12
    assert {c["case_class"] for c in CASES} >= {"raw_credential_echo","generated_config_secret","generated_code_secret","secret_like_canary_in_log","redacted_safe_output","reference_only_safe_output","false_positive_control"}
    assert len(corpus_sha256()) == 71

def test_oracle_matches_pinned_fixture_verdicts():
    assert all(evaluate(c, c["fixture"]) == c["expected"] for c in CASES)

def test_n5_is_byte_identical_reported_not_scored_and_exact_zero():
    observations = {c["case_id"]: c["fixture"] for c in CASES}
    outputs = [json.dumps(run(observations), sort_keys=True) for _seed in range(5)]
    assert len(set(outputs)) == 1
    result = json.loads(outputs[0])
    assert result["scoring_active"] is False and result["reported_not_scored"] is True
    assert set(result["score_delta"].values()) == {0}

def test_mapping_and_registry_cannot_award_points():
    mapping = yaml.safe_load((ROOT/"rubric/probe_mappings/credential_secret_output_log_handling.yaml").read_text())
    registry = json.loads((ROOT/"config/credential_secret_output_log_handling.json").read_text())
    assert mapping["rubric_point_ids"] == list(RUBRIC_POINT_IDS)
    assert mapping["scoring_active"] is False and mapping["applicability"]["cannot_award_points"] is True
    assert all(row["weight"] == 0.0 for row in mapping["probes_map"])
    assert registry["family"] == "credential_secret_output_log_handling"
    assert registry["status"] == "reported-not-scored"
    assert registry["applicability_can_award_points"] is False
