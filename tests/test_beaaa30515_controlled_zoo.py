"""BEAAA-30515: the scheduled zoo lane calls controlled targets, never Vapi.

The two properties this file exists to hold are the ones the Founder rejected
the previous result over: controlled target calls must actually occur, and
Vapi must never be constructed or called on the zoo path.
"""
from __future__ import annotations

import builtins
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from api.full_scan_worker import load_default_full_scan_registry_rows  # noqa: E402
from harness.controlled_zoo import (  # noqa: E402
    EXCLUDED_TARGETS,
    SURFACE_CENSUS_PATH,
    SURFACE_CENSUS_SHA256,
    ControlledZooError,
    ZOO_TARGETS,
    build_zoo_runner,
    census_compatible_targets,
    load_surface_census,
    route_registry_rows,
    select_target,
    zoo_targets,
)

VAPI_SECRETS = ("VAPI_API_KEY", "VAPI_ASSISTANT_ID")


@pytest.fixture(scope="module")
def census():
    return load_surface_census()


@pytest.fixture(scope="module")
def decisions(census):
    return route_registry_rows(load_default_full_scan_registry_rows(), census)


def test_census_matches_the_accepted_digest():
    import hashlib

    digest = hashlib.sha256(SURFACE_CENSUS_PATH.read_bytes()).hexdigest()
    assert digest == SURFACE_CENSUS_SHA256


def test_census_reproduces_the_accepted_counts(census):
    assert census["counts"]["applicable"] == 138
    assert census["counts"]["routed"] == 138
    assert census["counts"]["incomplete"] == 0
    assert len(census["families"]) == 138


def test_vapi_is_not_a_routable_zoo_target():
    assert "vapi" in EXCLUDED_TARGETS
    assert "vapi" not in zoo_targets()
    assert set(zoo_targets()) == {"mcp", "coding", "rag", "browser", "paperclip"}


def test_voice_families_route_nowhere_because_only_vapi_carries_voice(decisions):
    unrouted = {d.family: d.reason for d in decisions if d.target is None}
    assert set(unrouted) == {
        "voice_adversarial_pack",
        "voice_consent_recording_boundary",
        "voice_identity_channel_transfer",
        "voice_turn_taking_interruption_recovery",
    }
    for reason in unrouted.values():
        assert reason == "no_compatible_zoo_target:surfaces=voice"


def test_every_other_family_routes_and_all_five_surfaces_are_used(decisions):
    routed = [d for d in decisions if d.target is not None]
    assert len(routed) == len(decisions) - 4
    assert {d.target for d in routed} == set(zoo_targets())
    # Routing may never contradict the census the board accepted.
    compatible = census_compatible_targets(load_surface_census())
    for decision in routed:
        assert decision.target in compatible[decision.family]


def test_routed_families_carry_rows_to_dispatch(decisions):
    routed = [d for d in decisions if d.target is not None]
    assert all(d.rows for d in routed)
    assert sum(len(d.rows) for d in routed) > 0


def test_selection_prefers_the_least_capable_compatible_target():
    assert select_target(frozenset({"prose"})) == "rag"
    assert select_target(frozenset({"code_execution"})) == "coding"
    assert select_target(frozenset({"browser"})) == "browser"
    assert select_target(frozenset({"voice"})) is None
    # mcp and paperclip declare identical surfaces; both must be reachable.
    tied = {select_target(frozenset({"tool_calls"}), tie_index=i) for i in range(2)}
    assert tied == {"mcp", "paperclip"}


def test_a_tampered_census_is_refused(tmp_path):
    tampered = tmp_path / "census.json"
    payload = json.loads(SURFACE_CENSUS_PATH.read_text())
    payload["counts"]["applicable"] = 1
    tampered.write_text(json.dumps(payload))
    with pytest.raises(ControlledZooError, match="digest mismatch"):
        load_surface_census(tampered)


def test_building_an_excluded_target_is_refused(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    from harness.controlled_zoo import ZooTarget

    vapi = ZooTarget(
        name="vapi",
        surfaces=frozenset({"prose", "voice"}),
        system_prompt="",
        adapter_proof="harness.vapi_synthetic_capability_adapter",
    )
    with pytest.raises(ControlledZooError, match="excluded"):
        build_zoo_runner(vapi)


def test_controlled_runner_calls_the_target_and_reads_no_vapi_secret(monkeypatch):
    """The positive half: a zoo runner really does issue a target call."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    for name in VAPI_SECRETS:
        monkeypatch.delenv(name, raising=False)

    calls: list[str] = []

    class _Recorder:
        def complete(self, prompt: str) -> str:
            calls.append(prompt)
            return "controlled response"

    runner = build_zoo_runner(ZOO_TARGETS["mcp"])
    monkeypatch.setattr(runner, "_inner", _Recorder())
    monkeypatch.setattr(runner, "_system_prompt", None)

    assert runner.complete("probe") == "controlled response"
    assert calls == ["probe"]
    assert runner.model_id().startswith("openrouter:")


def test_zoo_path_never_imports_the_customer_agent_transport(monkeypatch):
    """The negative half, asserted at the import seam.

    ``harness.customer_runner`` is the only module that builds the Vapi HTTP
    transport. If the zoo lane never imports it, it cannot have constructed a
    Vapi target however the rest of the code changes.
    """
    for module in ("harness.controlled_zoo", "scheduled_scan"):
        sys.modules.pop(module, None)

    real_import = builtins.__import__
    forbidden: list[str] = []

    def _guarded(name, *args, **kwargs):
        if name == "harness.customer_runner" or name.endswith(".customer_runner"):
            forbidden.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guarded)
    zoo = importlib.import_module("harness.controlled_zoo")
    zoo.route_registry_rows(load_default_full_scan_registry_rows())
    monkeypatch.undo()

    assert forbidden == []


def test_zoo_secret_set_excludes_every_vapi_credential():
    import scheduled_scan

    assert set(scheduled_scan.ZOO_SECRET_ENV).isdisjoint(VAPI_SECRETS)
    assert "api.vapi.ai" not in scheduled_scan.ZOO_PREFLIGHT_TARGETS
    # The Vapi lane keeps its own unchanged secret and preflight sets.
    assert "api.vapi.ai" in scheduled_scan.PREFLIGHT_TARGETS


def test_zoo_lane_fails_closed_when_a_zoo_secret_is_missing(monkeypatch):
    import scheduled_scan

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ARE_BUNDLE_SIGNING_KEY", "x")
    monkeypatch.setenv("ARE_EVAL_PROVENANCE_KEY", "x")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        scheduled_scan.map_zoo_secret_environment()


def test_family_summary_accounts_for_unrouted_rows_by_reason(decisions):
    import scheduled_scan

    families, incomplete = scheduled_scan._zoo_family_summary(decisions, [])
    voice = families["voice_adversarial_pack"]
    assert voice["considered"] > 0
    assert voice["routed"] == 0
    assert voice["incomplete"] == voice["considered"]
    assert incomplete["unrouted_surface"] == sum(
        len(d.rows) for d in decisions if d.target is None
    )
    # Considered is never lost: every row is either routed or reason-coded.
    for counts in families.values():
        assert counts["considered"] == counts["routed"] + counts["incomplete"]


def test_zero_call_families_are_reason_coded_not_routed():
    """A route is only manifest-valid after both target and judge executed."""
    import scheduled_scan
    from harness.controlled_zoo import RoutingDecision

    decisions = [
        RoutingDecision("no-target", "rag", frozenset({"prose"}), "target=rag", ({},)),
        RoutingDecision("no-judge", "rag", frozenset({"prose"}), "target=rag", ({},)),
        RoutingDecision("covered", "rag", frozenset({"prose"}), "target=rag", ({},)),
    ]
    evidence = [
        {"family": "no-target", "target_calls": 0, "judge_calls": 0},
        {"family": "no-judge", "target_calls": 1, "judge_calls": 0},
        {"family": "covered", "target_calls": 1, "judge_calls": 1,
         "status": "complete", "verdict": "PASS"},
    ]

    effective = scheduled_scan._reason_code_uncovered_zoo_routes(decisions, evidence)
    families, _ = scheduled_scan._zoo_family_summary(effective, evidence)
    reasons = {d.family: d.reason for d in effective if d.target is None}

    assert reasons == {
        "no-target": "no_target_calls_observed",
        "no-judge": "no_judge_calls_observed",
    }
    assert families["no-target"]["routed"] == 0
    assert families["no-judge"]["routed"] == 0
    assert families["covered"]["routed"] == 1
    assert families["covered"]["target_called"] == 1
    assert families["covered"]["judge_calls"] == 1


def test_zoo_job_no_longer_raises_the_disabled_stub():
    import scheduled_scan

    source = Path(scheduled_scan.__file__).read_text()
    assert "scheduled zoo scans are disabled" not in source


def test_dispatch_records_real_target_calls_against_a_controlled_surface(
    tmp_path, monkeypatch, decisions
):
    """End-to-end: routed rows reach the executor and target_calls lands > 0.

    This is the assertion the rejected dispatcher smoke could not make. It runs
    the production dispatch path with the controlled surface stubbed at the
    runner seam, so the count comes from the executor's own budget ledger
    rather than from the stub.
    """
    import scheduled_scan
    from harness.runner import ModelRunner

    class _Controlled(ModelRunner):
        def __init__(self) -> None:
            self.calls = 0

        def model_id(self) -> str:
            return "controlled-zoo:test"

        def complete(self, prompt: str) -> str:
            self.calls += 1
            return "I cannot help with that request."

    subject = _Controlled()
    judge = _Controlled()
    monkeypatch.setattr(
        "harness.controlled_zoo.build_zoo_runner", lambda target, **kw: subject
    )
    monkeypatch.setattr(scheduled_scan, "default_judge_runner", lambda: judge)

    target_name = "rag"
    chosen = [d for d in decisions if d.target == target_name][:1]
    trimmed = [
        type(d)(d.family, d.target, d.required_surfaces, d.reason, d.rows[:2])
        for d in chosen
    ]
    config = scheduled_scan.ScheduleConfig(
        tenant_id="ariclaw",
        agent_endpoint="controlled-zoo://rag",
        evidence_root=tmp_path,
        scanner_version="0.5.0",
        corpus_version="0.2.0",
        seed=0,
        concurrency=1,
        rpm=30,
        retries=0,
        cost_cap_usd=100.0,
        timeout_seconds=600,
        judge_model="anthropic/claude-haiku-4.5",
    )
    finished, rows = scheduled_scan._run_zoo_target(
        zoo_targets()[target_name],
        trimmed,
        config=config,
        output_dir=tmp_path,
        run_date="2026-09-08",
        limiter=scheduled_scan.SharedRateLimiter(600),
    )

    assert rows, "dispatch produced no evidence rows"
    assert subject.calls > 0, "the controlled target was never called"
    assert sum(int(r.get("target_calls") or 0) for r in rows) > 0

    families, _ = scheduled_scan._zoo_family_summary(trimmed, rows)
    assert any(counts["target_called"] > 0 for counts in families.values())


def test_the_whole_dispatch_path_never_constructs_the_vapi_transport(
    tmp_path, monkeypatch, decisions
):
    """Guard the seam that actually leaked.

    ``build_registered_case_workers`` defaults its tau session factory to the
    customer-agent HTTP transport, so an otherwise fully controlled zoo run
    still constructed a Vapi runner. This test trips if that regresses through
    any branch, not just the one the earlier version happened to exercise.
    """
    import harness.customer_runner as customer_runner
    import scheduled_scan
    from harness.runner import ModelRunner

    def _forbidden(*args, **kwargs):
        raise AssertionError("the zoo lane constructed the customer/Vapi transport")

    monkeypatch.setattr(
        customer_runner, "customer_agent_http_runner_factory", _forbidden
    )
    for name in VAPI_SECRETS:
        monkeypatch.delenv(name, raising=False)

    class _Controlled(ModelRunner):
        def model_id(self) -> str:
            return "controlled-zoo:test"

        def complete(self, prompt: str) -> str:
            return "I cannot help with that request."

    monkeypatch.setattr(
        "harness.controlled_zoo.build_zoo_runner", lambda target, **kw: _Controlled()
    )
    monkeypatch.setattr(scheduled_scan, "default_judge_runner", _Controlled)

    config = scheduled_scan.ScheduleConfig(
        tenant_id="ariclaw",
        agent_endpoint="controlled-zoo://mcp",
        evidence_root=tmp_path,
        scanner_version="0.5.0",
        corpus_version="0.2.0",
        seed=0,
        concurrency=1,
        rpm=30,
        retries=0,
        cost_cap_usd=100.0,
        timeout_seconds=600,
        judge_model="anthropic/claude-haiku-4.5",
    )
    for target_name in sorted(zoo_targets()):
        chosen = [d for d in decisions if d.target == target_name][:1]
        trimmed = [
            type(d)(d.family, d.target, d.required_surfaces, d.reason, d.rows[:1])
            for d in chosen
        ]
        scheduled_scan._run_zoo_target(
            zoo_targets()[target_name],
            trimmed,
            config=config,
            output_dir=tmp_path / target_name,
            run_date="2026-09-08",
            limiter=scheduled_scan.SharedRateLimiter(600),
        )
