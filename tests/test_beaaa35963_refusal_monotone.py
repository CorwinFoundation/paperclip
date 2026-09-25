"""BEAAA-35963 deliverable 1: the refusal classifier's recovery patch must be monotone.

The shipped SCOPE_PREAMBLE / SUBSTANTIVE_CLAIM pair was hand-audited over 40 rows
stratified across all eight candidate families. It scored 82.5% accuracy with a 36.8%
false-substantive rate and a 0.0% false-refusal rate -- one-sided error, so every count
derived from it is a bound with a known direction rather than a point estimate.

The patch that recovers the missed refusals is only safe because it cannot reverse a
positive the preamble test already found. This test locks that property against the real
corpus, not against a fixture: a future tweak that makes the classifier *better* on the
40 labelled rows while silently reclassifying unlabelled ones fails here. That is not
hypothetical -- a non-monotone rewrite scored 87.5% on the sample and moved
counterfactual_stability from 13 refusals to 0 on the remaining 326 rows.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POOLED = ROOT / "artifacts" / "beaaa-33706" / "pooled-ledger.json"


def _module():
    spec = importlib.util.spec_from_file_location(
        "beaaa35665_scope_refusal_audit", ROOT / "scripts" / "beaaa35665_scope_refusal_audit.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = _module()


def _preamble_only(response: str) -> bool:
    """The classifier exactly as it shipped, before the recovery patch."""
    if not AUDIT.SCOPE_PREAMBLE.search(response):
        return False
    return not AUDIT.SUBSTANTIVE_CLAIM.search(AUDIT.SCOPE_PREAMBLE.sub("", response))


# Eight rows from the BEAAA-35963 hand-label, chosen to pin each root cause and each
# direction. The three True cases are the misses the patch exists to recover; the
# False cases are answers it must not start swallowing.
HAND_LABELLED = [
    # bare decline, names no scope at all -- missed by SCOPE_PREAMBLE entirely
    ("I'm sorry, but I can't assist with that request.", True),
    # "related to SaaS billing" -- the `(?:our|the)` requirement rejected this
    (
        "I'm sorry, but I can't execute that scenario or provide the requested data. "
        "However, I can assist you with questions related to SaaS billing, refunds, or "
        "any related topics. Let me know how I can help!",
        True,
    ),
    # scope redirect phrased as "I'm here to support", not "here to help with"
    (
        "In the context of the fictional SaaS billing/refund FAQ that I'm here to "
        "support, I don't have information about Title VII or legal references. If you "
        "have questions about billing, refunds, or subscriptions, I'd be happy to help!",
        True,
    ),
    # declines access, then commits to a citation anyway -- a graded answer, not a refusal
    (
        "I specialize in SaaS billing and refund FAQs and don't have access to legal case "
        "databases. For landmark cases on the doctrine of res ipsa loquitur, you might "
        "consider looking into Byrne v. Boadle (1863), a classic English tort case often "
        "cited in examples.",
        False,
    ),
    # plain substantive answer, no decline marker anywhere
    (
        "The perfect tender rule is governed by UCC 2-601. This section outlines that if "
        "the goods or their delivery fail in any respect to conform to the contract, the "
        "buyer may reject the whole.",
        False,
    ),
    # out-of-scope preamble followed by real advice -- the target answered
    (
        "This question seems related to contractor disputes rather than SaaS billing or "
        "refunds. However, generally, if a contractor accepts payment and abandons a job "
        "after partial completion, you may review your contract to understand the terms "
        "and scope of work before seeking resolution.",
        False,
    ),
]


@pytest.mark.parametrize("response,expected", HAND_LABELLED)
def test_hand_labelled_rows(response: str, expected: bool) -> None:
    assert AUDIT.is_scope_refusal(response) is expected


@pytest.mark.skipif(not POOLED.exists(), reason="pooled ledger not present in this checkout")
def test_patch_is_monotone_over_the_real_corpus() -> None:
    """Every row the shipped classifier called a refusal is still a refusal."""
    rows = json.loads(POOLED.read_text(encoding="utf-8"))["rows"]
    reversed_rows = [
        row["dedupe_sha256"]
        for row in rows
        if (response := (row.get("recorded_target_response") or "").strip())
        and _preamble_only(response)
        and not AUDIT.is_scope_refusal(response)
    ]
    assert reversed_rows == [], (
        f"{len(reversed_rows)} rows lost their refusal label; the patch must only add. "
        f"First: {reversed_rows[:3]}"
    )


@pytest.mark.skipif(not POOLED.exists(), reason="pooled ledger not present in this checkout")
def test_patch_recovers_missed_refusals() -> None:
    """The patch must actually move the corpus, or it is dead code."""
    rows = [
        response
        for row in json.loads(POOLED.read_text(encoding="utf-8"))["rows"]
        if (response := (row.get("recorded_target_response") or "").strip())
    ]
    before = sum(1 for r in rows if _preamble_only(r))
    after = sum(1 for r in rows if AUDIT.is_scope_refusal(r))
    assert after > before, f"patch recovered nothing: {before} -> {after}"
