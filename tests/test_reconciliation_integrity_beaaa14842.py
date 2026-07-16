"""BEAAA-14842 exact-case reconciliation integrity regressions."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from api.full_scan_executor import EvidenceAttemptRow, FullScanJobStore, build_attempt_manifest
from api.full_suite_manifest import FullSuiteCase


CASE = FullSuiteCase("framework.case_001", "P3.1")


def _job():
    return FullScanJobStore().submit(
        agent_id="fixture-agent",
        scanner_version="1.2.0",
        corpus_version="registry-baseline-2026-07-13",
        model_versions={"primary": "fixture-agent-v0", "judges": []},
        seed=0,
        registry_cases=(CASE,),
    )


def _row(*, probe_id=CASE.case_id, status="complete", evidence_ref="evidence://scan/case_001.json", incomplete_reason=None):
    return EvidenceAttemptRow(
        probe_id=probe_id,
        rubric_point_id=CASE.rubric_point_id,
        status=status,
        framework="fixture-worker@1.0.0",
        corpus_version="registry-baseline-2026-07-13",
        model_version="fixture-agent-v0",
        seed=0,
        evidence_ref=evidence_ref,
        incomplete_reason=incomplete_reason,
    )


def _manifest(row):
    return build_attempt_manifest(
        replace(_job(), attempts=("attempt-1",), evidence_rows=(row,)),
        finished_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize(
    "row",
    [
        _row(status="failed", evidence_ref="evidence://scan/failed.json", incomplete_reason="worker_error"),
        _row(status="skipped", evidence_ref="evidence://scan/skipped.json", incomplete_reason="approved_skip"),
        _row(status="incomplete", evidence_ref="evidence://scan/unsupported.json", incomplete_reason="unsupported"),
        _row(status="incomplete", evidence_ref="evidence://scan/timed-out.json", incomplete_reason="worker_timeout"),
        _row(status="complete", evidence_ref=None),
        _row(probe_id="framework.case_999"),
    ],
    ids=["failed", "skipped", "unsupported", "timed-out", "missing-evidence", "same-rubric-different-case"],
)
def test_invalid_attempt_rows_do_not_satisfy_required_case(monkeypatch, row):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-14842-test-key")

    manifest = _manifest(row)

    assert manifest["counts"]["missing_required"] == 1
    assert f"required_case_not_attempted:{CASE.case_id}" in manifest["incomplete_reasons"]
    assert manifest["bind_eligibility"] == "blocked_base_suite_incomplete"
    assert manifest["signed_manifest"].startswith("hmac-sha256:")


def test_exact_complete_evidenced_row_satisfies_required_case(monkeypatch):
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "beaaa-14842-test-key")

    manifest = _manifest(_row())

    assert manifest["counts"]["missing_required"] == 0
    assert manifest["incomplete_reasons"] == []
    assert manifest["bind_eligibility"] == "eligible"
    assert manifest["signed_manifest"].startswith("hmac-sha256:")
