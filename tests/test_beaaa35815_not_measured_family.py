"""BEAAA-35815: an all-removed family reports "not measured", never 0.0.

Follow-up to BEAAA-35665 (``d5490ad7e``, PR #731). The scope-refusal guard takes a row
out of the denominator when a scope-bounded agent declines an out-of-domain prompt, and
a removed row carries ``score: None`` so arithmetic on it raises instead of quietly
contributing a zero. At the family level that protection was dropped: ``score_family``
computed ``(n_pass / n_scored) if n_scored else 0.0``, so a family where the agent
correctly declined every prompt produced the same ``pass_rate`` as one where it answered
every prompt and got them all wrong -- the worst value on the curve.

The guard was inert when this was found (no case declares ``in_domain_requires_any``
yet), so this is a contract defect rather than a moved score. It would have become a live
scoring defect on the first declaration.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import harness.bench.scored_lane_common as bench  # noqa: E402

FAMILY = "p1_2_citation_integrity_golden"
REFUSAL = "I'm here to help with billing only."


@pytest.fixture(scope="module")
def probes():
    return bench.load_probes(bench.family_yaml_path(FAMILY))


def _all_declined(probes):
    guarded = [replace(p, in_domain_requires_any=("case law",)) for p in probes]
    return bench.score_family(guarded, {p.id: REFUSAL for p in probes}, FAMILY)


def _all_wrong(probes):
    """Same corpus, agent answers in-domain and gets every case wrong."""
    return bench.score_family(probes, {p.id: "the answer is 42" for p in probes}, FAMILY)


def test_all_removed_family_reports_no_pass_rate(probes):
    result = _all_declined(probes)
    assert result["n_guard_removed"] == result["n_prompts"] > 0
    assert result["n_scored"] == 0
    assert result["pass_rate"] is None
    assert result["family_disposition"] == "not_measured"


def test_a_measured_family_still_reports_a_number(probes):
    result = _all_wrong(probes)
    assert result["n_scored"] == result["n_prompts"] > 0
    assert result["pass_rate"] == 0.0
    assert result["family_disposition"] == "measured"


def test_declining_everything_is_distinguishable_from_failing_everything(probes):
    """The defect in one line: these two were byte-identical on ``pass_rate``."""
    declined = _all_declined(probes)
    wrong = _all_wrong(probes)
    assert declined["n_pass"] == wrong["n_pass"] == 0
    assert declined["pass_rate"] != wrong["pass_rate"]
    assert (declined["family_disposition"], wrong["family_disposition"]) == (
        "not_measured",
        "measured",
    )


def test_averaging_an_unmeasured_family_raises_instead_of_absorbing_a_zero(probes):
    """A consumer that averages without checking must fail loud, like the row level."""
    rates = [_all_declined(probes)["pass_rate"], _all_wrong(probes)["pass_rate"]]
    with pytest.raises(TypeError):
        sum(rates) / len(rates)


def test_envelope_carries_the_disposition(probes):
    for result in (_all_declined(probes), _all_wrong(probes)):
        envelope = bench.envelope_for_family(
            family_id=FAMILY, scoring_result=result, scanner_version="test"
        )
        assert envelope["pass_rate"] == result["pass_rate"] or (
            envelope["pass_rate"] is None and result["pass_rate"] is None
        )
        assert envelope["family_disposition"] == result["family_disposition"]
        bench.envelope_fingerprint(envelope)  # must not choke on a null pass_rate
