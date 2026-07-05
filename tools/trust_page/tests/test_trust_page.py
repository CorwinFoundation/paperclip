"""Tests for Trust Page v1 dynamic wiring (BEAAA-11926 / WI-3).

Covers: Supreme-Law envelope enforcement, stale-state machine transitions on the
real clock, warranty §2 rule -> pending posture, and the red-line prohibited-term
guard on rendered output.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools" / "post-bind-ingest" / "src"))

from tools.trust_page.verification_record import (  # noqa: E402
    MissingEnvelopeError, build_record, compute_stale_state, eligibility_effect,
)
from tools.trust_page.warranty_eligibility import evaluate_warranty_eligibility  # noqa: E402
from tools.trust_page.render import render_html, _assert_no_prohibited, PROHIBITED_TERMS  # noqa: E402
from tools.trust_page.warranty_eligibility import WarrantyEligibility  # noqa: E402

UTC = timezone.utc


def _score_response(scored_at="2026-06-01T00:00:00+00:00"):
    return {
        "are_total": 79,
        "band": {"display": "B", "posture": "Standard", "bands_version": "1.0", "letter": "B"},
        "rubric_version": "1.2.0",
        "scanner_version": "0.4.0",
        "corpus_version": "path_a_v2_1",
        "seed": 42,
        "scored_at": scored_at,
        "snapshot_hash": "sha256:" + "a" * 64,
        "contract_version": "are-scanner-api-v1@v1.3.0",
        "model_versions": {"m": "v1"},
    }


def _record(scored_at="2026-06-01T00:00:00+00:00", now=None, continuity="healthy", cadence=30):
    now = now or datetime(2026, 6, 10, tzinfo=UTC)
    warr = evaluate_warranty_eligibility(continuity_status=continuity, band_letter="B")
    return build_record(
        agent_name="Atlas", vendor_name="Northwind", score_response=_score_response(scored_at),
        rubric_locked_through="2026-08-04", continuity_status=continuity, warranty=warr,
        verification_cadence_days=cadence, now=now, generated_at=now.isoformat(),
    )


# --- Supreme Law: envelope completeness ------------------------------------------
def test_missing_envelope_field_refuses():
    sr = _score_response()
    del sr["seed"]  # drop a required envelope field
    warr = evaluate_warranty_eligibility(continuity_status="healthy")
    with pytest.raises(MissingEnvelopeError):
        build_record(
            agent_name="a", vendor_name="v", score_response=sr, rubric_locked_through="2026-08-04",
            continuity_status="healthy", warranty=warr, verification_cadence_days=30,
            now=datetime(2026, 6, 10, tzinfo=UTC), generated_at="x",
        )


def test_complete_envelope_stamped_on_record():
    rec = _record()
    for k in ("scanner_version", "corpus_version", "rubric_version", "rubric_locked_through", "seed", "bands_version"):
        assert rec.envelope[k] is not None
    assert rec.envelope["rubric_locked_through"] == "2026-08-04"


def test_empty_corpus_version_is_allowed():
    # offline fixtures (e.g. B1) legitimately carry corpus_version="".
    sr = _score_response()
    sr["corpus_version"] = ""
    warr = evaluate_warranty_eligibility(continuity_status="healthy")
    rec = build_record(
        agent_name="a", vendor_name="v", score_response=sr, rubric_locked_through="2026-08-04",
        continuity_status="healthy", warranty=warr, verification_cadence_days=30,
        now=datetime(2026, 6, 10, tzinfo=UTC), generated_at="x",
    )
    assert rec.envelope["corpus_version"] == ""


# --- stale-state machine ---------------------------------------------------------
def test_stale_state_transitions():
    v = datetime(2026, 6, 1, tzinfo=UTC)
    nrv = v + timedelta(days=30)
    assert compute_stale_state(v, nrv, v + timedelta(days=1)) == "current"
    assert compute_stale_state(v, nrv, v + timedelta(days=20)) == "aging"
    assert compute_stale_state(v, nrv, v + timedelta(days=27)) == "due_soon"
    assert compute_stale_state(v, nrv, v + timedelta(days=31)) == "stale"
    assert compute_stale_state(v, nrv, v + timedelta(days=75)) == "expired"


def test_stale_suspends_warranty_effect():
    assert eligibility_effect("stale", "pending") == "suspended"
    assert eligibility_effect("expired", "pending") == "suspended"
    assert eligibility_effect("current", "pending") == "pending"


# --- warranty §2 rule ------------------------------------------------------------
def test_warranty_pending_in_v1():
    w = evaluate_warranty_eligibility(continuity_status="healthy", band_letter="A")
    assert w.status == "pending"
    assert "ratified_minimum_band" in w.unmet_conditions


def test_warranty_never_eligible_without_ratification():
    # even a top band + healthy continuity cannot reach 'eligible' without program ratification.
    w = evaluate_warranty_eligibility(continuity_status="healthy", band_letter="A")
    assert w.status != "eligible"


def test_warranty_eligible_only_when_all_conditions_forced():
    forced = {k: True for k in (
        "ratified_minimum_band", "agent_at_or_above_band", "huw_agent_approval",
        "caps_reserve_allocated", "counsel_cleared")}
    w = evaluate_warranty_eligibility(continuity_status="healthy", inputs=forced)
    assert w.status == "eligible"


# --- red-line guard on rendered output -------------------------------------------
def test_render_has_no_prohibited_terms():
    doc = render_html(_record(), watermark="INTERNAL demo")
    _assert_no_prohibited(doc)  # must not raise
    low = doc.lower()
    for term in PROHIBITED_TERMS:
        assert f" {term} " not in f" {low} ", f"prohibited term leaked: {term}"


def test_guard_catches_injected_prohibited_term():
    with pytest.raises(ValueError):
        _assert_no_prohibited("<p>this agent has insurance coverage</p>")


def test_render_contains_envelope_and_band():
    doc = render_html(_record(), watermark="INTERNAL demo")
    assert "2026-08-04" in doc          # rubric lock stamped
    assert "seed 42" in doc             # seed stamped
    assert ">B<" in doc                 # band displayed
    assert "warranty remedy eligibility" in doc.lower()
