"""Lane-C families registered into the probe-mapping registry (Design A, step 1).

human_escalation_flagging (20), customer_defined_risk_pack (12), mcp_tool_integrity
(12) previously had metadata-only mapping stubs (0 probes) so build_registry() never
saw them. Their probes are now populated from the SPEC modules, so they appear as
registry rows — reported-not-scored and evidence-pending (target_bound False) until a
real capture run writes execution_evidence. Registering them must NOT move the frozen
production counts (340 / 362).
"""
from __future__ import annotations

import importlib

from tools.active_test_registry import (
    build_registry,
    count_active_executable,
    count_target_bound_runtime,
)

EXPECTED = {
    "human_escalation_flagging": 20,
    "customer_defined_risk_pack": 12,
    "mcp_tool_integrity": 12,
}


def _rows(rows, fam):
    return [r for r in rows if r["test_id"] == fam]


def test_lane_c_families_now_register_from_specs():
    rows = build_registry(include_content_candidates=False)
    for fam, n in EXPECTED.items():
        fr = _rows(rows, fam)
        assert len(fr) == n, (fam, len(fr))
        # row count matches the SPEC's embedded case count (source of truth)
        spec = importlib.import_module(f"evals.aiuc_path_b.{fam}").SPEC
        assert len(fr) == len(spec.cases), fam
        assert {r["reporting_status"] for r in fr} == {"reported_not_scored"}, fam
        assert {r["scoring_active"] for r in fr} == {False}, fam


def test_registration_is_evidence_pending_not_yet_runnable():
    rows = build_registry(include_content_candidates=False)
    for fam in EXPECTED:
        fr = _rows(rows, fam)
        # no execution_evidence yet -> target-bound False -> not counted / not runnable
        assert {r["execution_evidenced"] for r in fr} == {False}, fam
        assert {r["target_bound"] for r in fr} == {False}, fam
        assert count_active_executable(fr) == 0, fam
        assert count_target_bound_runtime(fr) == 0, fam


def test_frozen_production_counts_unchanged():
    rows = build_registry(include_content_candidates=False)
    assert count_active_executable(rows) == 340
    assert count_target_bound_runtime(rows) == 362
