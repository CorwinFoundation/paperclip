import json
from pathlib import Path

from tools.wave0_case_inventory import ROOT, load_inventory, mapping_rows, scenario_rows, verify


def test_canonical_wave0_inventory() -> None:
    inventory = ROOT / "artifacts/beaaa-15688/wave0-case-inventory.jsonl"
    reconciliation = json.loads((ROOT / "artifacts/beaaa-15688/wave0-reconciliation.json").read_text())
    assert load_inventory(inventory) == list(mapping_rows()) + list(scenario_rows())
    assert verify(load_inventory(inventory), reconciliation) == {
        "definitions": 715,
        "unique_hashes": 715,
        "duplicate_hashes": 0,
        "stable_ids": 715,
        "required_fields_complete": 715,
        "reconciled_current_behaviors": 180,
    }
