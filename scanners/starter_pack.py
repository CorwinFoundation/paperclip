"""Pinned identity and explicit case roster for the partner QA starter pack."""

from dataclasses import dataclass

STARTER_PACK_ID = "starter-100@1"
STARTER_PACK_TEST_COUNT = 100
STARTER_PACK_CORPUS_VERSION = "0.1.0"
STARTER_PACK_CORPUS_SNAPSHOT_HASH = (
    "sha256:112f3410a75d5f73d840c097054d510a7fcf962950a898452c927ac4ac0d3829"
)


@dataclass(frozen=True)
class StarterProbe:
    family_id: str
    case_id: str
    rubric_point_id: str


_FAMILIES = (
    ("p1_1_factuality_golden", "P1.1-FAC", "P1.1", 3),
    ("p1_3_consistency_golden", "P1.3-CON", "P1.3", 3),
    ("p1_4_refusal_golden", "P1.4-REF", "P1.4", 3),
    ("p1_5_calibration_golden", "P1.5-CAL", "P1.5", 3),
    ("p1_6_eval_coverage_breadth_golden", "P1.6-COV", "P1.6", 2),
    ("p2_5_sentiment_skew_golden", "P2.5-SKEW", "P2.5", 2),
    ("p3_2_indirect_injection_floor30", "P3.2-INJ30", "P3.2", 2),
    ("p3_3_secret_exfil_floor30", "P3.3-SEC30", "P3.3", 2),
    ("p3_5_multiturn_jailbreak", "P3.5-MT", "P3.5", 3),
    ("p3_6_output_attack", "P3.6-OUT", "P3.6", 3),
)

# The case IDs are deliberately constructed from pinned family namespaces and
# ordinal widths, never from registry traversal order. Changing this tuple is a
# starter-pack version change.
STARTER_PACK_ROSTER = tuple(
    StarterProbe(family, f"{prefix}-{ordinal:0{width}d}", point)
    for family, prefix, point, width in _FAMILIES
    for ordinal in range(1, 11)
)
STARTER_PACK_TEST_IDS = tuple(
    f"{row.family_id}:{row.case_id}" for row in STARTER_PACK_ROSTER
)
STARTER_PACK_FAMILY_COUNTS = {family: 10 for family, *_ in _FAMILIES}

assert len(STARTER_PACK_ROSTER) == STARTER_PACK_TEST_COUNT
assert len(set(STARTER_PACK_TEST_IDS)) == STARTER_PACK_TEST_COUNT

__all__ = [
    "STARTER_PACK_ID",
    "STARTER_PACK_TEST_COUNT",
    "STARTER_PACK_CORPUS_VERSION",
    "STARTER_PACK_CORPUS_SNAPSHOT_HASH",
    "STARTER_PACK_ROSTER",
    "STARTER_PACK_TEST_IDS",
    "STARTER_PACK_FAMILY_COUNTS",
    "StarterProbe",
]
