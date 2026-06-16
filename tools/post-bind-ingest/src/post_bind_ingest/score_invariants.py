"""Defense-in-depth `score_invariants` publish (BEAAA-5761 pattern).

Even though the post-bind ingest package contains no scoring path, every output
of the package carries a `score_invariants` block declaring the four
contract-output invariants of `are-scanner-api-v1@v1.0`:

    are_total_unchanged, subscores_unchanged, band_unchanged, confidence_unchanged

In this package they are unconditionally `True` because no module here imports
any score-yielding code path — enforced by `tests/test_no_score_imports.py`.
Detector children (BEAAA-5865/5866) inherit the pattern by importing
`SCORE_INVARIANTS_SCHEMA` and emitting the same block on every verdict.

If a future contributor introduces a score-mutating path inside this package,
the block stays at this declared shape but `assert_score_invariants_clean`
must be called with pre/post score snapshots — mirroring
`tools/portal_v1/portal.py::_build_peril_rollup`.
"""

from __future__ import annotations

from typing import Any, Mapping

SCORE_INVARIANTS_SCHEMA: tuple[str, ...] = (
    "are_total_unchanged",
    "subscores_unchanged",
    "band_unchanged",
    "confidence_unchanged",
)


def published_block() -> dict[str, bool]:
    """The unconditional defense-in-depth block emitted by this package.

    Returns the same four-boolean shape consumed by the BEAAA-5760 rollup and
    its observed-vs-predicted appendix (BEAAA-5867). Every value is `True`
    because no scoring path is touched.
    """
    return {key: True for key in SCORE_INVARIANTS_SCHEMA}


def assert_score_invariants_clean(
    pre: Mapping[str, Any] | None,
    post: Mapping[str, Any] | None,
) -> dict[str, bool]:
    """Compare a pre/post score snapshot pair, raise on mutation.

    Mirror of the BEAAA-5761 `_build_peril_rollup` invariant check. Returns the
    four-boolean block; raises `ScoreInvariantsViolated` if any field moved.

    The post-bind ingest package never calls this function in production paths
    (no score state passes through). It is exported so detector and sink
    children (BEAAA-5865/5866/5867) can wrap their own emit paths with the
    same defense-in-depth guard.
    """
    if pre is None and post is None:
        return published_block()
    if pre is None or post is None:
        raise ScoreInvariantsViolated(
            "score_invariants pre/post must both be None or both be mappings"
        )
    block = {
        "are_total_unchanged": pre.get("are_total") == post.get("are_total"),
        "subscores_unchanged": pre.get("subscores") == post.get("subscores"),
        "band_unchanged": pre.get("band") == post.get("band"),
        "confidence_unchanged": pre.get("confidence") == post.get("confidence"),
    }
    if not all(block.values()):
        raise ScoreInvariantsViolated(
            f"post-bind ingest mutated score invariants: {block}"
        )
    return block


class ScoreInvariantsViolated(Exception):
    """Raised when a pre/post score snapshot disagrees on any of the four invariants."""
