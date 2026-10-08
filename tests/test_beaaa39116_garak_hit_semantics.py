"""BEAAA-39116 G1/G2: garak hits are failed evaluations; no-answer turns cannot run."""

from __future__ import annotations

import pytest

from tests.test_native_agentdojo_garak_workers_beaaa16616 import _stub_garak
from tools.framework_worker_registry import WORKERS
from tools.framework_workers import GARAK_MIN_SAMPLE_SIZE, _garak_adapter_result


def _probe(score: float):
    class Probe:
        primary_detector = "stub.Detector"
        detector_scores = [score]

        def probe(self, gen):
            attempts = []
            for i in range(GARAK_MIN_SAMPLE_SIZE):
                gen._call_model(f"attack prompt {i}")
                attempts.append(object())
            return attempts

    return Probe


def _run(monkeypatch, score: float, reply: str):
    _stub_garak(monkeypatch, _probe(score))
    return _garak_adapter_result(
        WORKERS["garak"],
        {"case_id": "garak.misleading.FalseAssertion"},
        {"job_id": "j1", "seed": 0, "subject_adapter": {"response": reply}},
        "v1",
    )


def test_compliant_stub_has_zero_hits(monkeypatch):
    result = _run(monkeypatch, 0.0, "I can't help with that false claim.")
    assert result["status"] == "complete"
    assert result["hit_count"] == 0
    assert result["numeric_result"] == 0.0


def test_violating_stub_counts_every_failed_evaluation(monkeypatch):
    result = _run(monkeypatch, 1.0, "Sure, the moon is made of cheese.")
    assert result["status"] == "complete"
    assert result["hit_count"] == result["sample_size"] == GARAK_MIN_SAMPLE_SIZE
    assert result["numeric_result"] == 1.0


@pytest.mark.parametrize("reply", ["", "   ", "HTTP 503 Service Unavailable"])
def test_no_substantive_output_is_could_not_run_not_clean(monkeypatch, reply):
    result = _run(monkeypatch, 0.0, reply)
    assert result["status"] == "incomplete"
    assert result["incomplete_reason"].startswith(
        "could_not_run:garak_target_no_substantive_output:"
    )
    assert result["evidence_ref"] is None
    assert result["hit_count"] is None
    assert result["numeric_result"] is None
