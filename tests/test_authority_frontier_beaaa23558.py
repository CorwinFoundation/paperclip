import json
from pathlib import Path

from interaction_risk_shadow.authority_frontier import canonical_bytes, replay

PACK = Path(__file__).parent / "fixtures/interaction_risk_shadow/authority_frontier_pack_v3.json"


def test_corrected_authority_frontier_candidate_is_byte_exact_and_frozen():
    pack = json.loads(PACK.read_bytes())
    first = replay(pack)
    second = replay(json.loads(canonical_bytes(pack)))
    assert first == second
    result = json.loads(first)

    events = result["events"]
    assert [event["logical_time"] for event in events] == [1, 2, 3]
    assert [event["action_class"] for event in events] == ["monetary_low", "monetary_low", "destructive"]
    assert [event["caller_claimed_action_class"] for event in events] == ["read_only", "external_commit", "read_only"]

    refunds = events[:2]
    assert all(event["bundle_preauthorized"] for event in refunds)
    assert all(event["aggregate_reserve"] == 6 for event in refunds)
    assert all(event["budget_before"] == 3 == event["budget_after"] for event in refunds)
    assert result["released_member_count"] == 0

    proxy = events[2]
    assert proxy["canonical_specification"]["operation"] == "delete"
    assert proxy["action_class"] == "destructive"
    assert proxy["recommended_safe_default"] == "HUMAN_APPROVAL"

    assert all(event["reason_codes"] == ["BUNDLE_PREAUTHORIZED_SHADOW_ONLY"] for event in refunds)
    assert proxy["reason_codes"] == ["SHADOW_QUOTE_ELIGIBLE_NO_RELEASE"]
    assert all("right_censored" in event for event in events)
    assert result["frontier"]["right_censored"] is True
    assert result["enforcement_effect"] == "NONE"
    assert result["external_side_effect_count"] == 0
    assert result["protection_assertion"] == "NO_ACTIVE_PROTECTION_ASSERTION"
    assert result["ablations"] == {
        "paper_open": {
            "refund_splitting": "PER_ACTION_QUOTE",
            "proxy_bypass": "RAW_ALIAS_UNDERCLASSIFICATION",
        },
        "closed_v3": {
            "refund_splitting": "BOUNDARY_BUNDLE_PREAUTHORIZATION",
            "proxy_bypass": "STRICT_PROXY_LIFTING",
        },
    }
