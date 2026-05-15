"""Unit tests for the BEAAA-334 wall-regress POC-report renderer."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.beaaa334_wall_regress import render_compare_markdown


@pytest.fixture
def baseline_walled() -> dict:
    return {
        "started_at": "2026-05-14T20:15:00Z",
        "proxy_enabled": False,
        "lift": "0/3",
        "passes_ac": False,
        "results": [
            {"wall": "bland", "family": "cloud-IP edge refusal (Tier 1)",
             "walled": True, "elapsed_ms": 410, "wall_indicator": "status==403"},
            {"wall": "casetext", "family": "cloud-IP edge refusal (Tier 1)",
             "walled": True, "elapsed_ms": 520, "wall_indicator": "status==403"},
            {"wall": "chatlab", "family": "CF WAF adaptive flag (Tier 2)",
             "walled": True, "empty_rate": 0.45, "refused_count": 0,
             "wall_indicator": "empty_rate>0.20"},
        ],
    }


@pytest.fixture
def proxied_all_clear() -> dict:
    return {
        "started_at": "2026-05-15T03:00:00Z",
        "proxy_enabled": True,
        "lift": "3/3",
        "passes_ac": True,
        "results": [
            {"wall": "bland", "family": "cloud-IP edge refusal (Tier 1)",
             "walled": False, "elapsed_ms": 820, "wall_indicator": None},
            {"wall": "casetext", "family": "cloud-IP edge refusal (Tier 1)",
             "walled": False, "elapsed_ms": 1100, "wall_indicator": None},
            {"wall": "chatlab", "family": "CF WAF adaptive flag (Tier 2)",
             "walled": False, "empty_rate": 0.04, "refused_count": 0,
             "wall_indicator": None},
        ],
    }


def test_full_clear_report_renders(baseline_walled, proxied_all_clear):
    md = render_compare_markdown(baseline_walled, proxied_all_clear)
    assert "BEAAA-334" in md
    # Lift table reads correctly per wall.
    assert "| `bland` |" in md
    assert "| `casetext` |" in md
    assert "| `chatlab` |" in md
    # Latency delta computed and surfaced.
    assert "+410 ms" in md  # bland 820 - 410
    assert "+580 ms" in md  # casetext 1100 - 520
    # Headline + pass-AC verdict explicit.
    assert "passes_ac = True" in md
    assert "Headline lift:** 3/3" in md
    # ChatLab cell uses empty-rate metric, not ms.
    assert "empty_rate=0.45" in md
    assert "empty_rate=0.04" in md


def test_partial_lift_report(baseline_walled):
    """2/3 lift — Bland clears, Casetext still walled, ChatLab clears."""
    proxied = {
        "started_at": "2026-05-15T03:00:00Z",
        "proxy_enabled": True,
        "lift": "2/3",
        "passes_ac": True,
        "results": [
            {"wall": "bland", "family": "cloud-IP edge refusal (Tier 1)",
             "walled": False, "elapsed_ms": 820, "wall_indicator": None},
            {"wall": "casetext", "family": "cloud-IP edge refusal (Tier 1)",
             "walled": True, "elapsed_ms": 950, "wall_indicator": "status==403"},
            {"wall": "chatlab", "family": "CF WAF adaptive flag (Tier 2)",
             "walled": False, "empty_rate": 0.04, "refused_count": 0,
             "wall_indicator": None},
        ],
    }
    md = render_compare_markdown(baseline_walled, proxied)
    assert "Headline lift:** 2/3" in md
    # Per-wall lift verdicts surface the still-walled state.
    assert "still walled" in md  # Casetext didn't clear


def test_not_measurable_when_baseline_not_walled():
    """If baseline wasn't walled, lift is `not measurable` for that wall."""
    baseline = {
        "started_at": "2026-05-14T20:15:00Z",
        "proxy_enabled": False,
        "lift": "1/3",  # one wall not walled in baseline (unusual)
        "passes_ac": False,
        "results": [
            {"wall": "bland", "family": "cloud-IP edge refusal (Tier 1)",
             "walled": False, "elapsed_ms": 200, "wall_indicator": None},
        ],
    }
    proxied = {
        "started_at": "2026-05-15T03:00:00Z",
        "proxy_enabled": True,
        "lift": "1/3",
        "passes_ac": False,
        "results": [
            {"wall": "bland", "family": "cloud-IP edge refusal (Tier 1)",
             "walled": False, "elapsed_ms": 600, "wall_indicator": None},
        ],
    }
    md = render_compare_markdown(baseline, proxied)
    assert "not measurable" in md
