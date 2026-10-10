from decimal import Decimal
from pathlib import Path

import pytest

from harness.attacker_engine import AttackGoal
from tools.attacker_v1_live_campaign import (
    ATTACKER_MODEL,
    FIVE_FAMILIES,
    JUDGE_MODEL,
    CampaignConfig,
    CampaignPreflightError,
    OpenAICompatibleTarget,
    OpenRouterAttacker,
    OpenRouterJudge,
    ZeroCostAdapter,
    preflight_campaign,
    run_campaign,
)


def _config(tmp_path: Path, **overrides):
    goals = tuple(
        AttackGoal(f"goal-{index}", family, f"objective-{family}", "high")
        for index, family in enumerate(sorted(FIVE_FAMILIES))
    )
    values = dict(
        goals=goals,
        target_identity="target:reference-zoo-v1",
        per_attack_cap_usd=Decimal("1"),
        campaign_cap_usd=Decimal("20"),
        max_turns=1,
        run_count=1,
        output_dir=tmp_path,
    )
    values.update(overrides)
    return CampaignConfig(**values)


def test_preflight_rejects_missing_credentials_before_provider_calls(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    attacker = OpenRouterAttacker()
    target = OpenAICompatibleTarget("target-model")
    judge = OpenRouterJudge()

    with pytest.raises(CampaignPreflightError, match="credentials are absent"):
        preflight_campaign(_config(tmp_path), attacker, target, judge)


def test_preflight_rejects_identity_collision_and_over_cap(tmp_path):
    adapter = ZeroCostAdapter("same")
    with pytest.raises(CampaignPreflightError, match="identities collide"):
        preflight_campaign(_config(tmp_path, campaign_cap_usd=Decimal("0.01")), adapter, adapter, adapter)


def test_zero_cost_end_to_end_writes_attempts_frozen_metrics_and_transcripts(tmp_path):
    result = run_campaign(
        _config(tmp_path),
        attacker=ZeroCostAdapter(ATTACKER_MODEL),
        target=ZeroCostAdapter("target:reference-zoo-v1"),
        judge=ZeroCostAdapter(JUDGE_MODEL),
        commit="test-commit",
    )

    assert result.attempts == 5
    assert result.successes == 5
    assert result.attempts_path.read_text().count("\n") == 5
    assert result.frozen_successes_path.read_text().count("\n") == 5
    assert len(list(result.transcript_dir.glob("*.md"))) == 5
    assert len(result.metrics_path.read_text().splitlines()) > 1


def test_strong_controls_are_bounded_and_resume_without_duplicate_attempts(tmp_path):
    adapters = {
        "attacker": ZeroCostAdapter(ATTACKER_MODEL),
        "target": ZeroCostAdapter("target:reference-zoo-v1"),
        "judge": ZeroCostAdapter(JUDGE_MODEL),
    }
    config = _config(tmp_path, strong_control_repetitions=2, run_count=2)
    first = run_campaign(config, **adapters)
    second = run_campaign(config, **adapters)

    assert first.attempts == 12
    assert second.attempts == 0
    assert first.attempts_path.read_text().count("\n") == 12
