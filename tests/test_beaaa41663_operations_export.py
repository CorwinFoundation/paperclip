"""BEAAA-41663 P04 versioned export and pair-specific compatibility."""
from copy import deepcopy

import pytest

from api.operations_export import (
    OperationsExportError,
    build_scan_export,
    comparison_eligibility,
)


def _export(*, tenant="tenant-a", scan="scan-current", revision="result-2", cases=None):
    return build_scan_export(
        tenant_id=tenant,
        scan_id=scan,
        result_revision=revision,
        requested_target={"agent_id": "agent-1", "endpoint_hash": "sha256:requested"},
        measured_target={"agent_id": "agent-1", "endpoint_hash": "sha256:measured"},
        release={"scanner_version": "0.5.0", "build_digest": "sha256:release"},
        methodology_revision="rubric@1.3.0",
        corpus_revision="corpus@0.2.0",
        registry_revision="registry@1.0.0",
        case_revisions=(
            {"case-a": "a@1", "case-b": "b@1"} if cases is None else cases
        ),
        scope={"mode": "full", "complete": True, "held": False},
        evidence_refs=["evidence://b", "evidence://a", "evidence://a"],
        integrity_refs=["receipt://1"],
        persisted_result={"status": "complete", "score": 88},
    )


def test_export_carries_exact_pins_and_stable_result_digest():
    first = _export()
    second = _export()
    assert first == second
    assert first["version"] == "scan_export_v1"
    assert first["pins"]["case_revisions"] == {"case-a": "a@1", "case-b": "b@1"}
    assert first["evidence_refs"] == ["evidence://a", "evidence://b"]
    assert first["result_digest"].startswith("sha256:")
    assert len(first["result_digest"]) == 71


def test_export_requires_explicit_scope_completeness_and_case_revisions():
    with pytest.raises(OperationsExportError, match="case_revisions"):
        _export(cases={})
    export = _export()
    broken_scope = deepcopy(export)
    broken_scope["scope"].pop("complete")
    # Comparison consumes immutable exports; construction is where completeness fails closed.
    with pytest.raises(OperationsExportError, match="scope.complete"):
        build_scan_export(
            tenant_id="t", scan_id="s", result_revision="r",
            requested_target={}, measured_target={}, release={},
            methodology_revision="m", corpus_revision="c", registry_revision="g",
            case_revisions={"x": "1"}, scope={}, evidence_refs=[], integrity_refs=[],
            persisted_result={},
        )


def test_comparison_is_pair_specific_and_lists_case_exclusions():
    baseline = _export(scan="scan-baseline", revision="result-1", cases={
        "case-a": "a@1", "case-b": "b@1", "case-old": "old@1"
    })
    current = _export(cases={"case-a": "a@1", "case-b": "b@2", "case-new": "new@1"})
    eligibility = comparison_eligibility(current, baseline)
    assert eligibility["current"]["result_revision"] == "result-2"
    assert eligibility["baseline"]["result_revision"] == "result-1"
    assert eligibility["comparable_cases"] == [{"case_id": "case-a", "case_revision": "a@1"}]
    assert eligibility["exclusions"] == [
        {"case_id": "case-b", "reason": "case_revision_changed"},
        {"case_id": "case-new", "reason": "missing_baseline"},
        {"case_id": "case-old", "reason": "missing_current"},
    ]


def test_comparison_rejects_cross_tenant_pair():
    with pytest.raises(OperationsExportError, match="cross-tenant"):
        comparison_eligibility(_export(tenant="tenant-a"), _export(tenant="tenant-b"))
