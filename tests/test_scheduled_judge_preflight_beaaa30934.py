"""BEAAA-30934: a scheduled scan must refuse to dispatch without a judge.

``default_judge_runner`` returns a judge whose ``complete`` raises instead of
declining to build, so before this guard a credential-less scheduled run
dispatched every row, spent every target call, and produced rows with no
verdict at all -- the exact end state this lane is required to drive to zero.
"""

import pytest

from scripts.scheduled_scan import _require_available_judge


JUDGE_ENV = (
    "ARE_FULL_SCAN_JUDGE_PROVIDER",
    "ARE_FULL_SCAN_JUDGE_MODEL",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
)


@pytest.fixture
def clean_judge_env(monkeypatch):
    for name in JUDGE_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_anthropic_provider_without_key_fails_closed(clean_judge_env):
    clean_judge_env.setenv("ARE_FULL_SCAN_JUDGE_PROVIDER", "anthropic")
    clean_judge_env.setenv("ARE_FULL_SCAN_JUDGE_MODEL", "anthropic/claude-haiku-4.5")

    with pytest.raises(RuntimeError) as excinfo:
        _require_available_judge()

    message = str(excinfo.value)
    assert "no usable judge" in message
    assert "anthropic" in message
    assert "ANTHROPIC_API_KEY" in message


def test_openrouter_provider_without_key_fails_closed(clean_judge_env):
    clean_judge_env.setenv("ARE_FULL_SCAN_JUDGE_PROVIDER", "openrouter")

    with pytest.raises(RuntimeError, match="no usable judge"):
        _require_available_judge()


def test_no_credential_at_all_fails_closed(clean_judge_env):
    with pytest.raises(RuntimeError, match="no usable judge"):
        _require_available_judge()


def test_configured_anthropic_judge_is_accepted(clean_judge_env):
    clean_judge_env.setenv("ARE_FULL_SCAN_JUDGE_PROVIDER", "anthropic")
    clean_judge_env.setenv("ARE_FULL_SCAN_JUDGE_MODEL", "anthropic/claude-haiku-4.5")
    clean_judge_env.setenv("ANTHROPIC_API_KEY", "test-key-not-a-credential")

    model_id = _require_available_judge()

    assert model_id.startswith("anthropic:")


def test_configured_openrouter_judge_is_accepted(clean_judge_env):
    clean_judge_env.setenv("ARE_FULL_SCAN_JUDGE_PROVIDER", "openrouter")
    clean_judge_env.setenv("ARE_FULL_SCAN_JUDGE_MODEL", "openai/gpt-4o-mini")
    clean_judge_env.setenv("OPENROUTER_API_KEY", "test-key-not-a-credential")

    model_id = _require_available_judge()

    assert model_id != "judge:unavailable"
