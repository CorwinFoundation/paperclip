"""BEAAA-484 P4 GOVERNANCE Tier B vendor-evidence review wrapper.

Per-dim review that ingests a vendor questionnaire packet, runs the
existing P4 attestation scorer (scripts.score_p4_attestation, v0.1-LOCKED),
and emits one `evidence_refs` entry per GOVERNANCE rubric_point_id per the
v1.0 ARE Scanner API output schema.

Anonymous-path: when no questionnaire packet is supplied, no scoring is
attempted; all six GOV.* rubric points are surfaced in `uncovered_dimensions`
so the Underwriter prices Path B at worst-case (Underwriter hard guarantee #2,
contracts/are-scanner-api-v1.md@v1.0).

Wraps; does not fork. No scoring math here.
"""
from __future__ import annotations

from .review import (
    VERDICT_PASS,
    VERDICT_PARTIAL,
    VERDICT_FAIL,
    PATH_B_COVERAGE_CAP,
    review_vendor_packet,
    review_anonymous_path,
)

__all__ = [
    "VERDICT_PASS",
    "VERDICT_PARTIAL",
    "VERDICT_FAIL",
    "PATH_B_COVERAGE_CAP",
    "review_vendor_packet",
    "review_anonymous_path",
]
