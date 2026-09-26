"""BEAAA-36019: the fleet pool guard refuses a cap the account cannot cover."""
from __future__ import annotations

import json

import pytest

from harness.openrouter_pool import (
    LEGACY_SLICE,
    PoolExhausted,
    preflight,
    readings_for_slice,
    sole_drawer_delta,
)

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


# The two endpoints report different absolute totals -- the key's lifetime spend
# versus every key's -- so only their deltas are comparable.
PRIOR = {"at": "2026-09-26T02:23:59Z", "key_usage": 53.989678513, "total_usage": 122.360751439}


def test_sole_drawer_matches_when_only_this_key_drew():
    current = {"key_usage": 55.489678513, "total_usage": 123.860751439}
    sd = sole_drawer_delta(current, PRIOR)
    assert sd["matched"] is True
    assert sd["delta_key_usage_usd"] == sd["delta_account_usage_usd"] == 1.5
    assert sd["unexplained_usd"] == 0.0


def test_sole_drawer_flags_draw_this_key_cannot_explain():
    """A second key spending the pool shows up as account draw with no key draw."""
    current = {"key_usage": 53.989678513, "total_usage": 124.360751439}
    sd = sole_drawer_delta(current, PRIOR)
    assert sd["matched"] is False
    assert sd["unexplained_usd"] == 2.0


def test_sole_drawer_is_none_without_a_prior_key_reading():
    assert sole_drawer_delta({"key_usage": 1.0, "total_usage": 2.0}, None) is None
    assert sole_drawer_delta({"key_usage": 1.0, "total_usage": 2.0}, {"total_usage": 1.0}) is None


def test_a_mismatch_warns_but_does_not_refuse(tmp_path):
    """A second key on the account is not a defect in this caller's cap."""
    path = tmp_path / "pool-readings.jsonl"
    path.write_text(json.dumps(PRIOR) + "\n")
    pool = {
        "total_credits": 135.0,
        "total_usage": 124.360751439,
        "remaining_usd": 10.639249,
        "key_usage": 53.989678513,
        "key_label": "sk-or-v1-660...0c7",
    }
    reading = preflight(2.00, label="unit", slice_id="beaaa-36019", path=path, pool=pool)
    assert reading["decision"] == "pass"
    assert reading["sole_drawer"]["matched"] is False
    assert reading["sole_drawer"]["unexplained_usd"] == 2.0


def test_no_burn_rate_is_derived_from_a_total_usage_delta(tmp_path):
    """This account drains in discrete jumps; averaging across idle days invents
    a deadline. The reading carries OpenRouter's own windowed figures instead."""
    reading = preflight(
        1.00,
        label="unit",
        slice_id="beaaa-36019",
        path=tmp_path / "r.jsonl",
        pool={**POOL_2026_09_26, "key_usage_daily": 0, "key_usage_weekly": 1.886403853},
    )
    assert reading["key_usage_weekly"] == 1.886403853
    assert not any("per_day" in k or "burn" in k or "dry_by" in k for k in reading)
