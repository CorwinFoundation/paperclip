"""BEAAA-33708 round 2: the committed cohort is what the standing method produces.

These assert over ``artifacts/beaaa-33708/cohort-200.json`` as committed, not
over a fresh in-test selection alone. A selection script that is correct today
and a cohort file that was produced by some other means are indistinguishable
without this, and the cohort file is what the capture, the controls and the
ledger all key off.

The load-bearing ones are the prefix test and the reproduction test: together
they say the 200 rows are exactly the rows the rule names, in the order the rule
names them, with no room for a row to have been chosen or dropped by hand.
"""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path

import pytest

from tools import beaaa33673_zero_build_slice as Z
from tools import beaaa31092_content_slice as S

ROOT = Path(__file__).resolve().parents[1]
COHORT_PATH = ROOT / "artifacts/beaaa-33708/cohort-200.json"
PRIOR = ("artifacts/beaaa-33673/cohort-150.json", "artifacts/beaaa-33677/cohort-200.json")


@pytest.fixture(scope="module")
def cohort() -> dict:
    return json.loads(COHORT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def inventory() -> list[dict]:
    return list(S._jsonl(Z.PINNED_INVENTORY_PATH))


@pytest.fixture(scope="module")
def consumed() -> set[str]:
    ids: set[str] = set()
    for rel in PRIOR:
        ids |= {r["stable_case_id"] for r in json.loads((ROOT / rel).read_text(encoding="utf-8"))["rows"]}
    return ids


def _remaining_pool(inventory: list[dict], consumed: set[str]) -> dict[str, list[dict]]:
    by_family: dict[str, list[dict]] = collections.defaultdict(list)
    for row in sorted(inventory, key=lambda r: r["stable_case_id"]):
        if row["family_id"] in Z.SLICE_FAMILIES and row["stable_case_id"] not in consumed:
            by_family[row["family_id"]].append(row)
    return dict(by_family)


def test_inventory_pin_is_the_committed_one(cohort):
    raw = (ROOT / Z.PINNED_INVENTORY_PATH).read_bytes()
    assert cohort["inventory_sha256"] == hashlib.sha256(raw).hexdigest() == Z.INVENTORY_SHA256


def test_arithmetic_of_the_remainder(cohort, consumed):
    # 607 zero-build rows, 350 spent by the two prior rounds, 257 left, 200 taken,
    # 57 reserved for round 3. Stated as arithmetic so a silent re-draw shows up.
    assert len(consumed) == 350 == cohort["consumed_by_prior_slices"]
    assert cohort["zero_build_family_rows"] == 607
    assert cohort["pool_size"] == 257
    assert cohort["cohort_size"] == len(cohort["rows"]) == 200
    assert cohort["pool_size"] - cohort["cohort_size"] == 57


def test_no_row_is_reselected_from_a_prior_round(cohort, consumed):
    assert not {r["stable_case_id"] for r in cohort["rows"]} & consumed


def test_every_row_is_a_zero_build_inventory_row(cohort, inventory):
    by_case = {r["stable_case_id"]: r for r in inventory}
    for row in cohort["rows"]:
        source = by_case[row["stable_case_id"]]
        assert source["family_id"] == row["family_id"]
        assert row["family_id"] in Z.SLICE_FAMILIES


def test_each_family_takes_the_ascending_prefix_of_its_remaining_pool(cohort, inventory, consumed):
    """No cherry-picking inside a family.

    Proportional allocation fixes how MANY rows a family contributes; this fixes
    WHICH. Anything other than the ascending-stable_case_id prefix of what that
    family has left would let a round quietly prefer rows it expects to pass.
    """
    pool = _remaining_pool(inventory, consumed)
    chosen: dict[str, list[str]] = collections.defaultdict(list)
    for row in cohort["rows"]:
        chosen[row["family_id"]].append(row["stable_case_id"])
    for family, cases in chosen.items():
        expected = [r["stable_case_id"] for r in pool[family][:len(cases)]]
        assert cases == expected, family


def test_allocation_matches_the_rows_and_never_exceeds_the_pool(cohort, inventory, consumed):
    pool = _remaining_pool(inventory, consumed)
    counts = collections.Counter(r["family_id"] for r in cohort["rows"])
    assert dict(counts) == cohort["per_family_allocation"]
    assert sum(counts.values()) == 200
    for family, take in counts.items():
        assert take <= len(pool[family]), family
    # 18 drawing families, which is what the 4-per-family control budget of 72
    # was pre-registered against.
    assert len(counts) == 18


def test_no_intra_cohort_dedupe_collision(cohort, inventory):
    by_case = {r["stable_case_id"]: r for r in inventory}
    fingerprints = collections.Counter(
        by_case[r["stable_case_id"]]["semantic_behavior_fingerprint"] for r in cohort["rows"])
    assert [f for f, n in fingerprints.items() if n > 1] == []
    assert cohort["intra_cohort_dedupe_collisions"] == 0


def test_selection_reproduces_the_committed_file_byte_for_byte():
    from scripts import beaaa33708_select as sel  # noqa: PLC0415 - import cost is the point
    rebuilt = sel.select(200, "beaaa-33708-r1", "a77da8de")
    assert json.dumps(rebuilt, indent=1, sort_keys=True) + "\n" == COHORT_PATH.read_text(encoding="utf-8")
