"""BEAAA-39115: the operator summary names routing facts, never secret values."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import scheduled_scan  # noqa: E402


@dataclass(frozen=True)
class _D:
    family: str
    target: str | None
    reason: str
    rows: tuple


def test_routing_facts_count_reasons_and_never_leak_secrets() -> None:
    decisions = [
        _D("a", "rag", "target=rag", ({}, {})),
        _D("b", None, "no_compatible_zoo_target:surfaces=voice", ({},)),
        _D("c", None, "family_absent_from_surface_census", ({}, {}, {})),
    ]
    reports = {"rag": {"status": "completed", "rows": 2, "target_calls": 0,
                       "abort_reason": None, "families": ["a"], "scan_id": "x"}}
    env = {"OPENROUTER_API_KEY": "sk-or-SECRETVALUE", "ARE_BUNDLE_SIGNING_KEY": " "}
    facts = scheduled_scan._zoo_routing_facts(decisions, reports, env)
    assert facts["job_store_preexisting"] is None
    assert facts["families_with_target"] == 1
    assert facts["families_without_target"] == 2
    assert facts["rows_by_reason"] == {
        "family_absent_from_surface_census": 3, "no_compatible_zoo_target": 1, "routed": 2}
    assert facts["targets"]["rag"] == {
        "status": "completed", "rows": 2, "target_calls": 0, "abort_reason": None, "families": 1}
    assert facts["credentials_present"] == {
        "ARE_BUNDLE_SIGNING_KEY": False, "ARE_EVAL_PROVENANCE_KEY": False,
        "OPENROUTER_API_KEY": True, "ANTHROPIC_API_KEY": False}
    assert "SECRET" not in json.dumps(facts) and "sk-or" not in json.dumps(facts)


def test_routing_is_published_in_the_operator_summary() -> None:
    assert "routing" in scheduled_scan.OPERATOR_SUMMARY_FIELDS


def test_routing_facts_flag_a_same_day_replay() -> None:
    facts = scheduled_scan._zoo_routing_facts([], {}, {}, job_store_preexisting=True)
    assert facts["job_store_preexisting"] is True
