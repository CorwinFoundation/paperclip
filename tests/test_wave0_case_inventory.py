import json
from pathlib import Path
from unittest.mock import patch

from tools.wave0_case_inventory import (
    ROOT,
    load_inventory,
    mapping_rows,
    scenario_rows,
    verify,
    write_jsonl,
)


def test_canonical_wave0_inventory() -> None:
    inventory = ROOT / "artifacts/beaaa-15688/wave0-case-inventory.jsonl"
    reconciliation = json.loads(
        (ROOT / "artifacts/beaaa-15688/wave0-reconciliation.json").read_text(
            encoding="utf-8"
        )
    )
    assert load_inventory(inventory) == list(mapping_rows()) + list(scenario_rows())
    assert verify(load_inventory(inventory), reconciliation) == {
        "definitions": 715,
        "unique_hashes": 715,
        "duplicate_hashes": 0,
        "stable_ids": 715,
        "required_fields_complete": 715,
        "reconciled_current_behaviors": 180,
    }


def test_inventory_io_is_utf8_and_byte_stable() -> None:
    inventory = Path("inventory.jsonl")
    row = {"stable_case_id": "wave0.legal.\u00a7"}
    payload = json.dumps(row, ensure_ascii=False) + "\n"

    with patch.object(Path, "read_text", return_value=payload) as read_text:
        assert load_inventory(inventory) == [row]
    read_text.assert_called_once_with(encoding="utf-8")

    with patch.object(Path, "write_text") as write_text:
        write_jsonl(inventory, [row])
    write_text.assert_called_once_with(
        json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
