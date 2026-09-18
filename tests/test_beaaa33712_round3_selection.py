"""BEAAA-33712 round 3: the committed cohort is what the standing method produces.

These assert over ``artifacts/beaaa-33712/cohort-57.json`` as committed, not
over a fresh in-test selection alone. A selection script that is correct today
and a cohort file that was produced by some other means are indistinguishable
without this, and the cohort file is what the capture, the controls and the
ledger all key off.

The load-bearing ones are the prefix test and the reproduction test: together
they say the 57 rows are exactly the rows the rule names, in the order the rule
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
COHORT_PATH = ROOT / "artifacts/beaaa-33712/cohort-57.json"
PRIOR = ("artifacts/beaaa-33673/cohort-150.json", "artifacts/beaaa-33677/cohort-200.json",
         "artifacts/beaaa-33708/cohort-200.json")


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


def test_the_cohort_exhausts_the_pool_and_leaves_no_round_4(cohort, consumed):
    # 607 zero-build rows, 550 spent by the three prior rounds, 57 left, 57 taken.
    # pool_size == cohort_size is the whole claim of this round: there is no
    # remainder to defer a read to, so the sequence terminates here by arithmetic
    # and not by a judgement call made after the number was seen.
    assert len(consumed) == 550 == cohort["consumed_by_prior_slices"]
    assert cohort["zero_build_family_rows"] == 607
    assert cohort["pool_size"] == 57
    assert cohort["cohort_size"] == len(cohort["rows"]) == 57
    assert cohort["pool_size"] - cohort["cohort_size"] == 0


def test_every_remaining_zero_build_row_is_in_the_cohort(cohort, inventory, consumed):
    """Exhaustion stated from the inventory, not from the cohort's own header.

    ``pool_size`` is a field the selection script wrote. This recomputes the
    remainder straight from the pinned inventory and the three prior cohorts, so
    a row silently dropped from the pool before allocation would show up here
    even though the header would still read 57 == 57.
    """
    remaining = {r["stable_case_id"] for r in inventory
                 if r["family_id"] in Z.SLICE_FAMILIES and r["stable_case_id"] not in consumed}
    assert remaining == {r["stable_case_id"] for r in cohort["rows"]}


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
    assert sum(counts.values()) == 57
    for family, take in counts.items():
        assert take <= len(pool[family]), family
    # 17 drawing families, which is what the 4-per-family control budget of 68
    # was pre-registered against. Twelve of them contribute 1 to 3 rows, so at most
    # 37 controls can be drawn; the realized count lands short of 68 and is reported
    # short rather than topped up.
    assert len(counts) == 17
    assert sum(1 for n in counts.values() if n < 4) == 12
    assert sum(min(4, n) for n in counts.values()) == 37


def test_no_intra_cohort_dedupe_collision(cohort, inventory):
    by_case = {r["stable_case_id"]: r for r in inventory}
    fingerprints = collections.Counter(
        by_case[r["stable_case_id"]]["semantic_behavior_fingerprint"] for r in cohort["rows"])
    assert [f for f, n in fingerprints.items() if n > 1] == []
    assert cohort["intra_cohort_dedupe_collisions"] == 0


def test_selection_reproduces_the_committed_file_byte_for_byte():
    from scripts import beaaa33712_select as sel  # noqa: PLC0415 - import cost is the point
    rebuilt = sel.select(57, "beaaa-33712-r1", "d4fe997e")
    assert json.dumps(rebuilt, indent=1, sort_keys=True) + "\n" == COHORT_PATH.read_text(encoding="utf-8")
