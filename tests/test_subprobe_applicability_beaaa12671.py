"""BEAAA-12671 — P3.3 multi_tenant_leak sub-probe fires (and stays N/A) tests.

Proves:
  * multi_tenant_leak FIRES iff has_multi_tenant_context is true (AC#2);
  * a leaky multi-tenant agent -> verdict LEAK_DETECTED; a resistant one ->
    RESISTED (the fired probe discriminates);
  * multi_tenant_leak is N/A when the flag is false, resolves the canonical
    P3.3 justification, and does NOT invoke the runner (no false-fires, AC#3);
  * requires_flag gates are honoured across every secret_exfil sub-probe;
  * execution is deterministic under fixed input (supports N=5 delta = 0 at
    the sub-probe level).

No scoring-path code is imported — this asserts probe-fires behaviour only.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness import subprobe_applicability as sp
from harness.runner import MockRunner

REPO = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO / "fixtures" / "multi_tenant"
MARKER = "SIBLING-TENANT-CANARY-7F3A9C21"


@pytest.fixture(scope="module")
def family():
    return sp.load_family()


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


def _runner_from_fixture(fixture: dict, family: dict) -> MockRunner:
    seeds = {s.id: s.payload for s in sp._seeds_for(family, sp.MULTI_TENANT_CATEGORY)}
    by_seed = fixture.get("responses_by_seed_id", {})
    payload_to_response = {seeds[sid]: resp for sid, resp in by_seed.items() if sid in seeds}
    return MockRunner(
        fixture["surface_id"],
        lambda prompt: payload_to_response.get(prompt, "I can't share other users' conversations."),
    )


class _ExplodingRunner(MockRunner):
    """Fails the test if complete() is ever called — proves N/A skips the runner."""

    def __init__(self):
        super().__init__("exploding", lambda p: p)

    def complete(self, prompt: str) -> str:  # pragma: no cover - must never run
        raise AssertionError("runner.complete called on an N/A sub-probe (false fire)")


def test_multi_tenant_leak_fires_and_detects_leak(family):
    fx = _load_fixture("customer_service_multitenant_leaky.json")
    result = sp.run_multi_tenant_leak(
        _runner_from_fixture(fx, family),
        sibling_tenant_marker=MARKER,
        flags=fx["architecture_flags"],
        family=family,
    )
    assert result["fired"] is True
    assert result["status"] == "MEASURED"
    assert result["verdict"] == "LEAK_DETECTED"
    assert result["n_cross_tenant_leaks"] == result["n_seeds"] > 0
    # transcript is real evidence: every seed executed, marker present
    assert {r["seed_id"] for r in result["transcript"]} == {"sx.mt.01", "sx.mt.02"}
    assert all(r["cross_tenant_leak"] for r in result["transcript"])


def test_multi_tenant_leak_fires_but_resists(family):
    fx = _load_fixture("customer_service_multitenant_resistant.json")
    result = sp.run_multi_tenant_leak(
        _runner_from_fixture(fx, family),
        sibling_tenant_marker=MARKER,
        flags=fx["architecture_flags"],
        family=family,
    )
    assert result["fired"] is True
    assert result["verdict"] == "RESISTED"
    assert result["n_cross_tenant_leaks"] == 0
    assert all(not r["cross_tenant_leak"] for r in result["transcript"])


def test_multi_tenant_leak_na_when_flag_false_and_runner_never_called(family):
    fx = _load_fixture("customer_service_single_tenant.json")
    result = sp.run_multi_tenant_leak(
        _ExplodingRunner(),
        sibling_tenant_marker=MARKER,
        flags=fx["architecture_flags"],
        family=family,
    )
    assert result["fired"] is False
    assert result["status"] == "N/A"
    assert result["na_justification_key"] == "P3.3.no_secret_bearing_surface"
    # N/A justification resolves from the canonical policy (not duplicated here)
    assert result["na_justification"]
    assert "P3.3" in result["na_justification"]
    assert result["transcript"] == []


def test_requires_flag_honoured_across_all_subprobes(family):
    all_false = {
        "has_system_prompt_in_response_channel": False,
        "has_credentials_in_prompt": False,
        "has_honeytoken_canary_planted": False,
        "has_multi_tenant_context": False,
    }
    decisions = sp.subprobe_decisions(family, all_false)
    # every gated sub-probe is N/A when its flag is false
    assert all(not d.fires for d in decisions.values())
    assert decisions["multi_tenant_leak"].requires_flag == "has_multi_tenant_context"

    only_mt = dict(all_false, has_multi_tenant_context=True)
    d2 = sp.subprobe_decisions(family, only_mt)
    assert d2["multi_tenant_leak"].fires is True
    # flipping multi-tenant does NOT fire the unrelated system-prompt sub-probe
    assert d2["system_prompt_disclosure"].fires is False


def test_missing_required_flag_raises(family):
    with pytest.raises(sp.SubProbeError):
        sp.subprobe_decisions(family, {})  # multi_tenant_leak's flag absent


def test_execution_is_deterministic(family):
    fx = _load_fixture("customer_service_multitenant_leaky.json")
    runs = [
        sp.run_multi_tenant_leak(
            _runner_from_fixture(fx, family),
            sibling_tenant_marker=MARKER,
            flags=fx["architecture_flags"],
            family=family,
        )
        for _ in range(5)
    ]
    serialized = {json.dumps(r, sort_keys=True) for r in runs}
    assert len(serialized) == 1  # N=5 byte-identical
