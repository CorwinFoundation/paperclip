"""Tests for the Founder digest plain-language layer (BEAAA-598).

The renderer's house-style guardrails live in `tools/founder-digest/glossary.py`.
These tests pin three properties that the CTO will sample on review:

1. First-use gloss applies once per page, not recursively.
2. BEAAA-N ticket ids are stripped from body copy and surfaced for the
   footer index.
3. Deprecated codenames (BMI / BEAI / etc.) are canonicalised before the
   gloss layer runs.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


_GLOSSARY_PATH = Path(__file__).resolve().parents[1] / "tools" / "founder-digest" / "glossary.py"


@pytest.fixture(scope="module")
def glossary():
    spec = importlib.util.spec_from_file_location("founder_digest_glossary_test", _GLOSSARY_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules["founder_digest_glossary_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_first_use_gloss_applies_once_per_page(glossary):
    g = glossary.PageGlosser()
    out = g.apply("ARE Scanner is the engine. ARE Scanner again. IFA is us.")
    assert "our agent risk scanner (ARE Scanner)" in out
    assert out.count("our agent risk scanner") == 1
    assert "us (IFA)" in out
    assert "the scanner again" in out  # short-form after first use


def test_are_scanner_does_not_recursively_match_are(glossary):
    """Critical: ARE Scanner gloss must not trigger ARE re-substitution."""
    g = glossary.PageGlosser()
    out = g.apply("ARE Scanner")
    assert out == "our agent risk scanner (ARE Scanner)"
    assert "our agent risk score" not in out


def test_reset_clears_per_page_state(glossary):
    g = glossary.PageGlosser()
    page1 = g.apply("ARE Scanner")
    g.reset()
    page2 = g.apply("ARE Scanner")
    assert page1 == page2 == "our agent risk scanner (ARE Scanner)"


def test_strip_ticket_ids_collects_in_order(glossary):
    cleaned, ids = glossary.strip_ticket_ids(
        "See BEAAA-12 and BEAAA-7 for context. Also BEAAA-12 again."
    )
    assert ids == ["BEAAA-12", "BEAAA-7"]
    assert "BEAAA-" not in cleaned


def test_strip_ticket_ids_drops_stranded_prepositions(glossary):
    cleaned, _ = glossary.strip_ticket_ids(
        "HoUW substance-side per BEAAA-513."
    )
    assert cleaned == "HoUW substance-side."


def test_strip_ticket_ids_cleans_empty_parens(glossary):
    cleaned, _ = glossary.strip_ticket_ids("Tier B2 POC (BEAAA-387 child).")
    assert "()" not in cleaned
    assert cleaned == "Tier B2 POC (child)."


def test_canonicalize_replaces_deprecated_codenames(glossary):
    assert glossary.canonicalize("BMI scoring") == "ARE scoring"
    assert glossary.canonicalize("BEAI Model Protector v0.1") == "ARE Scanner v0.1"
    assert glossary.canonicalize("Agent Readiness Index") == "ARE"
    assert "BEAI" not in glossary.canonicalize("BEAI ships next week")


def test_find_anti_patterns_catches_house_style_violations(glossary):
    hits = glossary.find_anti_patterns(
        "In today's evolving landscape, our trusted partner unlocks value."
    )
    assert "in today's" in hits
    assert "trusted partner" in hits
    assert "unlocking value" not in hits  # different phrase


def test_find_anti_patterns_clean_body(glossary):
    body = "Scanner v0.1 holds at 0.7 calibration variance on the golden set."
    assert glossary.find_anti_patterns(body) == []


def test_find_deprecated_terms_catches_legacy(glossary):
    hits = glossary.find_deprecated_terms("the BMI score is solid")
    assert any("BMI" in h for h in hits)
