"""Defense-in-depth: `score_invariants` block from BEAAA-5761 pattern is published.

Per CTO brief §5 AC#6: even though no scoring path is touched by this
package, the BEAAA-5761 four-boolean defense-in-depth block must be
importable, schema-pinned, and unconditionally True from `published_block()`.
Detector children (BEAAA-5865/5866) inherit this pattern by importing
`SCORE_INVARIANTS_SCHEMA` and emitting the same block on every verdict.
"""

from __future__ import annotations

import pytest

from post_bind_ingest.score_invariants import (
    SCORE_INVARIANTS_SCHEMA,
    ScoreInvariantsViolated,
    assert_score_invariants_clean,
    published_block,
)


def test_schema_pinned_to_four_invariants() -> None:
    assert SCORE_INVARIANTS_SCHEMA == (
        "are_total_unchanged",
        "subscores_unchanged",
        "band_unchanged",
        "confidence_unchanged",
    )


def test_published_block_is_all_true() -> None:
    block = published_block()
    assert set(block.keys()) == set(SCORE_INVARIANTS_SCHEMA)
    assert all(block.values())


def test_pre_post_clean_returns_block() -> None:
    pre = {"are_total": 88, "subscores": {"GOV": 22}, "band": "B", "confidence": 0.9}
    post = {"are_total": 88, "subscores": {"GOV": 22}, "band": "B", "confidence": 0.9}
    block = assert_score_invariants_clean(pre, post)
    assert block == {key: True for key in SCORE_INVARIANTS_SCHEMA}


def test_pre_post_drift_raises() -> None:
    pre = {"are_total": 88, "subscores": {"GOV": 22}, "band": "B", "confidence": 0.9}
    post = {"are_total": 87, "subscores": {"GOV": 22}, "band": "B", "confidence": 0.9}
    with pytest.raises(ScoreInvariantsViolated):
        assert_score_invariants_clean(pre, post)


def test_none_pair_returns_published_block() -> None:
    block = assert_score_invariants_clean(None, None)
    assert block == published_block()


def test_one_sided_none_raises() -> None:
    with pytest.raises(ScoreInvariantsViolated):
        assert_score_invariants_clean(None, {"are_total": 88})
