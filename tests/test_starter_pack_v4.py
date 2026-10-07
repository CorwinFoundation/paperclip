"""starter-100@4 scenario bank (BEAAA-38986)."""

import hashlib
import json
from collections import Counter

from scanners.starter_pack_v3 import STARTER_V3_CASES, STARTER_V3_RUBRICS
from scanners.starter_pack_v4 import (
    STARTER_V4_BANK,
    STARTER_V4_PER_FAMILY,
    STARTER_V4_RUBRICS,
    STARTER_V4_UPSTREAM_PENDING,
    case_from_payload,
    payload_hash,
    starter_v4_scenarios,
    starter_v4_strata,
)

PINNED_BANK_SHA256 = "c63771156219cfbc212246ccb9cea6a307cbeb905a53f7a3895090cf9c79f702"


def _families():
    return Counter(p["family_id"] for _s, p, _c in STARTER_V4_BANK)


def test_bank_hash_is_pinned():
    blob = json.dumps([[s, p, c] for s, p, c in STARTER_V4_BANK], sort_keys=True).encode()
    assert hashlib.sha256(blob).hexdigest() == PINNED_BANK_SHA256


def test_every_family_has_ten_draws_and_room_for_a_fresh_form():
    strata = starter_v4_strata()
    assert len(strata) == 10
    assert sum(s["count"] for s in strata) == 100
    counts = _families()
    for family_id, n in counts.items():
        assert n >= 3 * STARTER_V4_PER_FAMILY, family_id


def test_v3_rows_replay_unchanged():
    by_id = {p["case_id"]: p for _s, p, _c in STARTER_V4_BANK}
    for case in STARTER_V3_CASES:
        assert case_from_payload(by_id[case.case_id]) == case


def test_canaries_are_fresh_and_bound_to_their_own_prompt():
    rows = [p for _s, p, _c in STARTER_V4_BANK]
    canaries = [p["canary"] for p in rows if p["canary"]]
    assert len(canaries) == len(set(canaries))
    for p in rows:
        if p["canary"]:
            assert p["canary"] in p["prompt"]
        others = [c for c in canaries if c != p["canary"]]
        assert not any(c in p["prompt"] for c in others), p["case_id"]


def test_no_duplicate_prompts_and_rubrics_unchanged():
    prompts = [p["prompt"] for _s, p, _c in STARTER_V4_BANK]
    assert len(prompts) == len(set(prompts))
    assert STARTER_V4_RUBRICS == STARTER_V3_RUBRICS


def test_scenarios_carry_stable_hashes_and_known_strata():
    strata = {s["stratum_id"] for s in starter_v4_strata()}
    for sc in starter_v4_scenarios():
        assert sc["test_spec_id"]
        assert sc["calibration_status"] == "uncalibrated"
        assert sc["stratum_id"] in strata
        assert len(payload_hash(sc["payload"])) == 64
        assert payload_hash(sc["payload"]) == payload_hash(dict(sc["payload"]))
