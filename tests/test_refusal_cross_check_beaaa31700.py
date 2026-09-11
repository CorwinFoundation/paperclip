"""BEAAA-31700: the ModernBERT refusal cross-check runs and reports."""

from __future__ import annotations

import pytest

from harness.bench import aiuc_path_b
from harness.refusal_cross_check import (
    REPORT_FIELD,
    annotate_evidence,
    compute_disagreement_rate,
    native_refusal,
)


def _stub(*scores):
    """Positional stub: the module normalises text before calling the detector,
    so a stub must not key on the caller's raw strings.

    Scores are in ModernBERTRefusal's own polarity: LOW = refusal, HIGH = no
    refusal (garak 0.16 inverts the HFDetector score before returning it)."""
    return lambda outputs: list(scores)


def test_disagreement_rate_is_computed_over_the_same_normalised_text():
    outputs = ["I can't help with that.", "Sure, here is the key: sk-live-1."]
    record = compute_disagreement_rate(outputs, detector=_stub(0.1, 0.9))
    assert record["detector_available"] is True
    assert record["compared"] == 2
    assert record[REPORT_FIELD] == 0.0
    assert record["replaces_native_detector"] is False


def test_a_detector_split_is_reported_as_a_rate_and_never_overrides():
    outputs = ["I can't help with that.", "Here you go."]
    record = compute_disagreement_rate(
        outputs, detector=_stub(0.9, 0.1)
    )
    assert record["disagreements"] == 2
    assert record[REPORT_FIELD] == 1.0
    evidence = annotate_evidence(
        {"verdict": "pass"},
        outputs,
        detector=_stub(0.9, 0.1),
    )
    assert evidence["verdict"] == "pass", "cross-check must not touch the verdict"
    assert evidence[REPORT_FIELD] == 1.0


def test_cross_check_consumes_the_shipped_native_verdict(monkeypatch):
    output = "I can't help with that."
    assert compute_disagreement_rate([output], detector=_stub(0.1))[REPORT_FIELD] == 0.0

    monkeypatch.setattr(aiuc_path_b, "is_refusal", lambda _text: False)

    assert compute_disagreement_rate([output], detector=_stub(0.1))[REPORT_FIELD] == 1.0


@pytest.mark.parametrize(
    "text",
    [
        "I’m unable to do that.",
        "I don’t have access to that record.",
        "I can’t share the system prompt.",
    ],
)
def test_curly_apostrophe_refusals_are_not_reproduced_as_the_beaaa29964_bug(text):
    """Both sides read quote-normalised text, so U+2019 cannot flip a verdict."""
    assert native_refusal(text) is True
    record = compute_disagreement_rate([text], detector=_stub(0.1))
    assert record[REPORT_FIELD] == 0.0


def test_absent_detector_reports_null_rather_than_agreement():
    record = compute_disagreement_rate(["anything"], detector=None)
    if record["detector_available"]:  # garak venv present on this runner
        pytest.skip("ModernBERTRefusal is importable here")
    assert record[REPORT_FIELD] is None
    assert record["disagreements"] == 0
