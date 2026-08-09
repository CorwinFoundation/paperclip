"""Tests for the eval-framework evidence-capture harness (BEAAA-WAVE1B-DRAFT)."""
from __future__ import annotations

import pytest

from harness import eval_framework_capture as cap


def test_plan_lists_only_pending_framework_probes():
    grouped = cap.plan()
    # every listed family is a known capture backend
    assert set(grouped).issubset(set(cap.CAPTURE_BACKENDS))
    # agentdojo and the bias metrics are pending; pyrit is fully evidenced (absent)
    assert "agentdojo" in grouped
    assert "pyrit" not in grouped
    assert sum(len(v) for v in grouped.values()) >= 20


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


def test_capture_refuses_to_synthesize_when_target_present_but_no_runner(monkeypatch):
    monkeypatch.setenv("SCANNER_TARGET_URL", "https://example.test")
    monkeypatch.setenv("SCANNER_TARGET_MODEL", "m")
    monkeypatch.setenv("SCANNER_TARGET_KEY", "k")
    # no runner wired -> must raise NotImplementedError, never invent evidence
    with pytest.raises(NotImplementedError):
        cap.capture("agentdojo", provenance="BEAAA-1")
