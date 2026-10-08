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


def test_routing_facts_count_upstream_status_and_fingerprint_key() -> None:
    """BEAAA-39088: name the aborting HTTP status without leaking the key."""
    import hashlib

    rows = [
        {"incomplete_reason": "upstream_http_402: insufficient credits"},
        {"incomplete_reason": "upstream_http_402"},
        {"reason": "upstream_http_429"},
        {"incomplete_reason": "format"},
        "not-a-row",
    ]
    env = {"OPENROUTER_API_KEY": "sk-or-secret"}
    facts = scheduled_scan._zoo_routing_facts([], {}, env, evidence_rows=rows)
    assert facts["upstream_http_by_status"] == {
        "upstream_http_402": 2,
        "upstream_http_429": 1,
    }
    expected = hashlib.sha256(b"sk-or-secret").hexdigest()[:12]
    assert facts["openrouter_key_sha256_prefix"] == expected
    assert "sk-or-secret" not in repr(facts)
    assert scheduled_scan._zoo_routing_facts([], {}, {})["openrouter_key_sha256_prefix"] is None


def test_rotated_openrouter_key_gets_a_fresh_zoo_job_key() -> None:
    """BEAAA-39088: a rotated key re-dispatches the same day; same key resumes."""
    old = scheduled_scan._zoo_credential_epoch({"OPENROUTER_API_KEY": "sk-or-dead"})
    new = scheduled_scan._zoo_credential_epoch({"OPENROUTER_API_KEY": "sk-or-live"})
    assert old != new and old.startswith(":or-") and len(old) == len(":or-") + 12
    assert old == scheduled_scan._zoo_credential_epoch({"OPENROUTER_API_KEY": " sk-or-dead\n"})
    assert "sk-or" not in old + new
    assert scheduled_scan._zoo_credential_epoch({}) == ""
