"""BEAAA-36019: the fleet pool guard refuses a cap the account cannot cover."""
from __future__ import annotations

import json

import pytest

from harness.openrouter_pool import LEGACY_SLICE, PoolExhausted, preflight, readings_for_slice

POOL_2026_09_26 = {"total_credits": 135.0, "total_usage": 122.36, "remaining_usd": 12.64}


def test_cap_under_pool_passes_and_records(tmp_path):
    path = tmp_path / "pool-readings.jsonl"
    reading = preflight(
        2.00, label="unit", slice_id="beaaa-36019", path=path, pool=POOL_2026_09_26
    )
    assert reading["decision"] == "pass"
    assert reading["remaining_usd"] == 12.64
    assert json.loads(path.read_text().strip())["cap_usd"] == 2.0


def test_cap_over_pool_refuses_with_both_numbers(tmp_path):
    path = tmp_path / "pool-readings.jsonl"
    with pytest.raises(PoolExhausted) as exc:
        preflight(20.00, label="unit", slice_id="beaaa-36019", path=path, pool=POOL_2026_09_26)
    msg = str(exc.value)
    assert "$20.00" in msg and "$12.64" in msg
    # The refusal is still a reading: the burn rate stays derivable.
    assert json.loads(path.read_text().strip())["decision"] == "refused"


def test_key_limit_remaining_is_not_the_pool(tmp_path):
    """The seat key reported $21.01 headroom against a $12.64 account. Cap $15
    is spendable by the key and not by the account; the guard refuses it."""
    with pytest.raises(PoolExhausted):
        preflight(
            15.00,
            label="unit",
            slice_id="beaaa-36019",
            path=tmp_path / "r.jsonl",
            pool=POOL_2026_09_26,
        )


def test_legacy_rows_belong_to_the_35938_slice(tmp_path):
    path = tmp_path / "pool-readings.jsonl"
    path.write_text(
        json.dumps({"drawn_since_baseline_usd": 1.636633, "remaining_usd": 12.647867}) + "\n"
    )
    preflight(1.00, label="unit", slice_id="beaaa-36019", path=path, pool=POOL_2026_09_26)
    assert len(readings_for_slice(LEGACY_SLICE, path=path)) == 1
    assert len(readings_for_slice("beaaa-36019", path=path)) == 1


def test_non_positive_cap_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        preflight(0.0, label="unit", slice_id="x", path=tmp_path / "r.jsonl", pool=POOL_2026_09_26)
