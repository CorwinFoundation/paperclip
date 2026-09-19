"""BEAAA-34122 focused live-account isolation-escape integration proof."""
import json

from harness.containment_escape_chain import DEFAULT_SEEDS
from harness.live_containment_escape_chain import (
    LIVE_CASE_ID, LiveAccountFixture, run_live_containment_escape_chain,
)
from tools.probe_registry import build_registry


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def test_resistant_and_vulnerable_live_fixtures_seeds_zero_through_four_are_stable():
    first = []
    second = []
    for results in (first, second):
        for seed in DEFAULT_SEEDS:
            resistant = LiveAccountFixture(resistant=True)
            vulnerable = LiveAccountFixture(resistant=False)
            resisted = run_live_containment_escape_chain(resistant, credential="live-secret", seed=seed)
            escaped = run_live_containment_escape_chain(vulnerable, credential="live-secret", seed=seed)
            assert resistant.canary_hits == []
            assert resisted["verdict"] == "resisted"
            assert escaped["verdict"] == "chain_observed"
            assert vulnerable.canary_hits == [f"owned-canary://seed-{seed}"]
            assert [hop["operation"] for hop in escaped["evidence"]["ordered_hops"]] == [
                "boundary_transition", "network_egress", "canary_action",
            ]
            assert all(hop["credential"] == "[REDACTED]" for hop in escaped["evidence"]["ordered_hops"])
            assert all(hop["evidence_ref"].startswith("evidence://") for hop in escaped["evidence"]["ordered_hops"])
            assert b"live-secret" not in _bytes(escaped)
            results.append((resisted["evidence_fingerprint"], escaped["evidence_fingerprint"]))
    assert first == second


def test_active_registry_marks_exact_live_case_reported_not_scored():
    registry = build_registry()
    row = next(row for row in registry["rows"] if row["case_id_or_probe_id"] == LIVE_CASE_ID)
    assert row["corpus_or_family"] == "tool_misuse_scope_escape"
    assert row["categories"]["reported"] is True
    assert row["categories"]["scoring_active"] is False

