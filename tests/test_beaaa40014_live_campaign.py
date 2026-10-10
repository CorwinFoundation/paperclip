from decimal import Decimal
from pathlib import Path

import pytest

from harness.attacker_engine import AttackGoal
from harness.reference_zoo import REFERENCE_SPECS
from tools.attacker_v1_live_campaign import (
    ATTACKER_MODEL,
    FIVE_FAMILIES,
    JUDGE_MODEL,
    PARENT_CAMPAIGN_CAP_USD,
    CampaignConfig,
    CampaignPreflightError,
    OpenAICompatibleTarget,
    OpenRouterAttacker,
    OpenRouterJudge,
    ProviderError,
    ZeroCostAdapter,
    build_reference_zoo_target,
    _message_text,
    preflight_campaign,
    run_campaign,
)


class RecordingTransport:
    def __init__(self):
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        from tools.attacker_v1_live_campaign import MeteredChat
        return MeteredChat("ok", Decimal("0"))


def test_reference_zoo_targets_use_provider_models_and_distinct_profiles():
    profiles = {}
    for agent_id in (
        "zoo-strong-control-v1", "zoo-refuse-everything-v1", "zoo-prompt-leak-v1",
        "zoo-injection-following-v1", "zoo-policy-invention-v1",
    ):
        transport = RecordingTransport()
        target = build_reference_zoo_target(agent_id, transport=transport)
        target.send("hello")
        call = transport.calls[0]
        assert call["model"] == "anthropic/claude-sonnet-4.6"
        assert not call["model"].startswith("target/")
        assert target.identity == f"reference-zoo:{agent_id}"
        profiles[agent_id] = call["messages"][0]["content"]
    assert len(set(profiles.values())) == 5


def test_reasoning_only_provider_turn_is_usable_attacker_text():
    assert _message_text({"content": None, "reasoning": " next bounded attack "}) == "next bounded attack"
    assert _message_text({"content": "", "reasoning_details": [{"type": "reasoning.text", "text": "fallback"}]}) == "fallback"


def test_provider_turn_without_content_or_reasoning_is_a_provider_error():
    with pytest.raises(ProviderError, match="no usable content or reasoning text"):
        _message_text({"content": None, "reasoning_details": []})


def _config(tmp_path: Path, **overrides):
    goals = tuple(
        AttackGoal(f"goal-{index}", family, f"objective-{family}", "high")
        for index, family in enumerate(sorted(FIVE_FAMILIES))
    )
    values = dict(
        goals=goals,
        target_identities={spec.agent_id: f"mock:model-{index}" for index, spec in enumerate(REFERENCE_SPECS)},
        per_attack_cap_usd=Decimal("1"),
        campaign_cap_usd=Decimal("20"),
        max_turns=1,
        run_count=1,
        strong_control_repetitions=3,
        output_dir=tmp_path,
    )
    values.update(overrides)
    return CampaignConfig(**values)


def _targets():
    return {spec.agent_id: ZeroCostAdapter(f"model-{index}") for index, spec in enumerate(REFERENCE_SPECS)}


def test_preflight_allows_reference_targets_to_share_the_approved_provider_model(tmp_path):
    attacker = ZeroCostAdapter("attacker-model")
    judge = ZeroCostAdapter("judge-model")
    targets = {spec.agent_id: build_reference_zoo_target(spec.agent_id, transport=RecordingTransport()) for spec in REFERENCE_SPECS}
    config = _config(tmp_path, campaign_cap_usd=Decimal("30"), target_identities={agent_id: target.identity for agent_id, target in targets.items()})

    report = preflight_campaign(config, attacker, targets, judge)

    assert report.campaign_cap_usd == Decimal("30")


def test_preflight_rejects_missing_credentials_before_provider_calls(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    attacker = OpenRouterAttacker()
    targets = {spec.agent_id: OpenAICompatibleTarget(f"target-model-{index}", identity=spec.agent_id) for index, spec in enumerate(REFERENCE_SPECS)}
    judge = OpenRouterJudge()

    with pytest.raises(CampaignPreflightError, match="credentials are absent"):
        preflight_campaign(_config(tmp_path, target_identities={spec.agent_id: spec.agent_id for spec in REFERENCE_SPECS}), attacker, targets, judge)


def test_preflight_rejects_identity_collision_and_over_cap(tmp_path):
    adapter = ZeroCostAdapter("same")
    with pytest.raises(CampaignPreflightError, match="identities collide"):
        preflight_campaign(_config(tmp_path, campaign_cap_usd=Decimal("0.01"), target_identities={spec.agent_id: adapter.identity for spec in REFERENCE_SPECS}), adapter, {spec.agent_id: adapter for spec in REFERENCE_SPECS}, adapter)


def test_preflight_enforces_the_usd_30_parent_cap(tmp_path):
    with pytest.raises(CampaignPreflightError, match=f"parent cap {PARENT_CAMPAIGN_CAP_USD}"):
        preflight_campaign(
            _config(tmp_path, campaign_cap_usd=Decimal("30.01")),
            ZeroCostAdapter(ATTACKER_MODEL),
            _targets(),
            ZeroCostAdapter(JUDGE_MODEL),
        )


def test_zero_cost_end_to_end_writes_attempts_frozen_metrics_and_transcripts(tmp_path):
    result = run_campaign(
        _config(tmp_path),
        attacker=ZeroCostAdapter(ATTACKER_MODEL),
        targets=_targets(),
        judge=ZeroCostAdapter(JUDGE_MODEL),
        commit="test-commit",
    )

    assert result.attempts == 35
    assert result.successes == 35
    rows = [__import__("json").loads(line) for line in result.attempts_path.read_text().splitlines()]
    assert {row["agent_id"] for row in rows} == {spec.agent_id for spec in REFERENCE_SPECS}
    assert sum(row["agent_id"] == "zoo-strong-control-v1" for row in rows) == 15
    assert result.frozen_successes_path.read_text().count("\n") == 5
    assert len(list(result.transcript_dir.glob("*.md"))) == 35
    assert len(result.metrics_path.read_text().splitlines()) > 1


def test_strong_controls_are_bounded_and_resume_without_duplicate_attempts(tmp_path):
    adapters = {
        "attacker": ZeroCostAdapter(ATTACKER_MODEL),
        "targets": _targets(),
        "judge": ZeroCostAdapter(JUDGE_MODEL),
    }
    config = _config(tmp_path, strong_control_repetitions=3, run_count=2)
    first = run_campaign(config, **adapters)
    second = run_campaign(config, **adapters)

    assert first.attempts == 55
    assert second.attempts == 0
    assert first.attempts_path.read_text().count("\n") == 55
    assert len(__import__("json").loads(second.metrics_path.read_text())) == 25


def test_generic_target_cannot_masquerade_as_reference_zoo(tmp_path):
    generic = ZeroCostAdapter("generic")
    targets = {spec.agent_id: generic for spec in REFERENCE_SPECS}
    config = _config(tmp_path, target_identities={spec.agent_id: generic.identity for spec in REFERENCE_SPECS})

    with pytest.raises(CampaignPreflightError, match="identities collide"):
        preflight_campaign(config, ZeroCostAdapter(ATTACKER_MODEL), targets, ZeroCostAdapter(JUDGE_MODEL))
