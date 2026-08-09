import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("beaaa19929", ROOT / "scripts/build_beaaa19929_probe_vapi_backlog.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_canonical_backlog_reconciles_exactly_once():
    manifest = json.loads(MODULE.MANIFEST.read_text())
    rows = MODULE.read_jsonl(MODULE.LEDGER)
    assert len(rows) == len({row["stable_case_id"] for row in rows}) == 2000
    assert manifest["integrity"] == {
        "unique_stable_case_ids": 2000,
        "duplicates": 0,
        "orphans": 0,
        "missing": 0,
        "source_maps_per_row": 3,
    }
    assert manifest["current_counts"]["status"] == {
        "adjudicated_valid": 95,
        "adjudication_unresolved": 30,
        "capability_trace_not_behavior_scored": 808,
        "deterministic_invalid": 642,
        "deterministic_valid": 255,
        "judge_uncovered_response_absent": 170,
    }
    assert manifest["current_counts"]["verdict"] == {"FAIL": 56, "INVALID": 642, "PARTIAL": 10, "PASS": 284}


@pytest.mark.parametrize("mutation", ["duplicate", "orphan"])
def test_duplicate_and_orphan_inputs_fail_closed(tmp_path, mutation):
    rows = MODULE.read_jsonl(MODULE.FREEZE)
    if mutation == "duplicate":
        rows.append(rows[0])
        with pytest.raises(ValueError):
            MODULE.index_unique(rows, "authorization freeze")
    else:
        rows[0] = {**rows[0], "stable_case_id": "orphan.case.id"}
        canonical = set(MODULE.index_unique(MODULE.read_jsonl(MODULE.SOURCE_INVENTORY), "canonical inventory"))
        mutated = set(MODULE.index_unique(rows, "authorization freeze"))
        assert mutated - canonical == {"orphan.case.id"}
        assert canonical - mutated
