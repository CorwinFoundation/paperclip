import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "artifacts/beaaa-19518/vapi-closeout-terminal-ledger-v1.1.json"


def build():
    path = ROOT / "scripts/build_beaaa19518_vapi_closeout_v1_1.py"
    spec = importlib.util.spec_from_file_location("build_beaaa19518", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()
    return json.loads(LEDGER.read_text())


def test_exact_unique_rows_and_category_sums():
    data = build()
    judged = [row for family in data["family_scorecard"] for row in family["full_judged_row_ids"]]
    assert len(judged) == len(set(judged)) == 350
    assert sum(f["full_judged_count"] for f in data["family_scorecard"]) == 350
    assert data["deterministic_invalid"]["count"] == 642
    assert data["uncovered_judge_rows"]["count"] == 170


def test_terminal_records_have_pointers_and_remedies():
    data = build()
    for row in data["deterministic_invalid"]["records"]:
        assert row["family_specific_evidence"]
        assert row["missing_capability"]
        assert row["attempted_remedy"]
    for row in data["uncovered_judge_rows"]["records"]:
        assert row["evidence_pointer"]
        assert row["missing_capability_or_evidence"]


def test_denominators_cannot_be_conflated():
    data = build()
    denominators = data["denominators"]
    assert denominators["valid_behavior_judged"]["not_an_overall_vapi_backbond_score"] is True
    assert denominators["valid_behavior_judged"]["total"] == 350
    assert denominators["observed_vapi_target_attempted"] == 1192
    assert denominators["synthetic_capability_evidence"] == 596
    assert data["baseline_delta"]["equation"] == "255 + 95 = 350"
