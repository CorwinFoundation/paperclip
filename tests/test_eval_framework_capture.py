"""Tests for the eval-framework evidence-capture harness (BEAAA-WAVE1B-DRAFT)."""
from __future__ import annotations

import json

import pytest

from harness import eval_framework_capture as cap
from tools import active_test_registry as registry


def test_plan_lists_only_pending_framework_probes():
    grouped = cap.plan()
    # every listed family is a known capture backend
    assert set(grouped).issubset(set(cap.CAPTURE_BACKENDS))
    # pyrit is fully evidenced (absent); committed sidecar evidence may remove
    # completed BEAAA-21704 families from the pending plan.
    assert "pyrit" not in grouped
    assert sum(len(v) for v in grouped.values()) <= 24


def test_capture_fails_closed_without_target(monkeypatch):
    for k in ("SCANNER_TARGET_URL", "SCANNER_TARGET_MODEL", "SCANNER_TARGET_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(cap.TargetNotConfigured):
        cap.capture("agentdojo", provenance="BEAAA-1")


def test_build_evidence_block_requires_every_field():
    with pytest.raises(ValueError):
        cap.build_evidence_block(
            concrete_input="in", expected_oracle="orc",
            recorded_response="", verdict="pass", provenance="BEAAA-1",
        )
    block = cap.build_evidence_block(
        concrete_input="in", expected_oracle="orc",
        recorded_response="resp", verdict="pass", provenance="BEAAA-1",
    )
    assert set(block) == set(cap.EVIDENCE_FIELDS)


def test_capture_uses_real_default_runner_when_target_present(monkeypatch):
    monkeypatch.setenv("SCANNER_TARGET_URL", "https://example.test")
    monkeypatch.setenv("SCANNER_TARGET_MODEL", "m")
    monkeypatch.setenv("SCANNER_TARGET_KEY", "k")
    monkeypatch.setattr(cap, "pending_probes", lambda framework: [])
    assert cap.capture("agentdojo", provenance="BEAAA-1") == 0


def test_beaaa21704_exact_roster_is_real_and_reported_not_scored():
    records = json.loads(cap.EVIDENCE_PATH.read_text(encoding="utf-8"))
    expected = {f"{family}:{case}" for family, case in registry.BEAAA_21704_REPORTED_NOT_SCORED_CASES}
    assert set(records) == expected
    rows = {
        (row["test_id"], row["case_id"]): row
        for row in registry.build_registry(include_content_candidates=False)
    }
    selected = [rows[key] for key in registry.BEAAA_21704_REPORTED_NOT_SCORED_CASES]
    assert len(selected) == 24
    assert all(row["production_runner_executable"] is True for row in selected)
    assert all(row["execution_evidenced"] is True for row in selected)
    assert all(row["reporting_status"] == "reported_not_scored" for row in selected)
    assert all(row["scoring_active"] is False for row in selected)


@pytest.mark.parametrize(
    ("updates", "status"),
    [
        ({"recorded_target_response": ""}, "missing_execution_evidence:recorded_target_response"),
        ({"recorded_target_response": "placeholder target output"}, "invalid_execution_evidence:non_execution_response"),
        ({"recorded_target_response": "fabricated evidence"}, "invalid_execution_evidence:non_execution_response"),
        ({"recorded_target_response": "fixture presented as production"}, "invalid_execution_evidence:non_execution_response"),
        ({"github_provenance": "BEAAA-1"}, "invalid_execution_evidence:provenance"),
        ({"target_class": "synthetic_fixture"}, "invalid_execution_evidence:target_class"),
        ({"executed_at": "not-a-time"}, "invalid_execution_evidence:executed_at"),
    ],
)
def test_external_evidence_rejects_non_execution_claims(updates, status):
    evidence = {
        "concrete_input_or_action": "input",
        "expected_oracle": "oracle",
        "recorded_target_response": "actual target response",
        "verdict": "OBSERVED_REPORTED_NOT_SCORED",
        "github_provenance": "BEAAA-21704",
        "target_identity": "qwen2.5:3b",
        "target_class": "approved_non_customer_production_like_bounded_agent",
        "executed_at": "2026-08-10T15:00:00Z",
    }
    evidence.update(updates)
    probe = {"probe_id": "case", "execution_evidence": evidence}
    valid, reason = registry._execution_evidence_status(
        probe, {"benchmark": "family"}, allow_external_evidence=False
    )
    # Force the same validation branch used by the external sidecar without I/O.
    if valid:
        response = evidence["recorded_target_response"]
        if registry._NON_EXECUTION_MARKERS.search(response):
            valid, reason = False, "invalid_execution_evidence:non_execution_response"
        elif evidence["github_provenance"] != registry.BEAAA_21704_PROVENANCE:
            valid, reason = False, "invalid_execution_evidence:provenance"
        elif evidence["target_class"] not in registry.BEAAA_21704_ALLOWED_TARGET_CLASSES:
            valid, reason = False, "invalid_execution_evidence:target_class"
        else:
            try:
                registry.dt.datetime.fromisoformat(evidence["executed_at"].replace("Z", "+00:00"))
            except ValueError:
                valid, reason = False, "invalid_execution_evidence:executed_at"
    assert (valid, reason) == (False, status)
