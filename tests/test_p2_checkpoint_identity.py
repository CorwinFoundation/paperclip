from pathlib import Path

import pytest

from harness.p2_checkpoint_identity import checkpoint_identity, runner_identity
from harness.runner import MockRunner, OpenAIRunner


class IdentifiedMock(MockRunner):
    def p2_checkpoint_identity(self):
        return {'fixture_version': 'frozen-v1', 'response': 'ok'}


def test_unknown_runner_refused():
    with pytest.raises(ValueError, match='does not support'):
        runner_identity(MockRunner('same-display-name', lambda _: 'different behavior'))


def test_identity_tracks_configuration_and_file_bytes(tmp_path):
    corpus = tmp_path / 'corpus'
    corpus.write_text('first')
    runner = IdentifiedMock('fixture', lambda _: 'ok')
    def identity(**config):
        return checkpoint_identity(runners={'subject': runner}, files=[corpus],
                                   configuration=config)
    original = identity(ensemble_n=3)
    assert original == identity(ensemble_n=3)
    assert original != identity(ensemble_n=1)
    corpus.write_text('second')
    assert original != identity(ensemble_n=3)


def test_native_identity_tracks_generation_settings(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    one = OpenAIRunner('pinned', seed=1, system_prompt='first')
    two = OpenAIRunner('pinned', seed=2, system_prompt='first')
    assert runner_identity(one) != runner_identity(two)
    assert 'test-only' not in str(runner_identity(one))
    class CustomOpenAI(OpenAIRunner):
        pass
    with pytest.raises(ValueError):
        runner_identity(CustomOpenAI('pinned'))


def test_invalid_custom_identity_refused():
    runner = IdentifiedMock('fixture', lambda _: 'ok')
    runner.p2_checkpoint_identity = lambda: {'opaque': object()}
    with pytest.raises(TypeError):
        runner_identity(runner)


@pytest.mark.parametrize('setting', ['OPENAI_BASE_URL', 'OPENAI_ORG_ID',
                                    'OPENAI_PROJECT_ID', 'HTTPS_PROXY'])
def test_native_identity_tracks_ambient_routing_without_disclosure(monkeypatch, setting):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    runner = OpenAIRunner('pinned')
    original = runner_identity(runner)
    monkeypatch.setenv(setting, 'sensitive-routing-value')
    changed = runner_identity(runner)
    assert changed != original
    assert 'sensitive-routing-value' not in str(changed)
    assert 'test-only' not in str(changed)


def test_native_identity_tracks_sdk_version(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    runner = OpenAIRunner('pinned')
    monkeypatch.setattr('harness.p2_checkpoint_identity.metadata.version', lambda _: '1')
    original = runner_identity(runner)
    monkeypatch.setattr('harness.p2_checkpoint_identity.metadata.version', lambda _: '2')
    assert runner_identity(runner) != original


def test_checkpoint_identity_tracks_transitive_runtime_dependencies(monkeypatch):
    runner = IdentifiedMock('fixture', lambda _: 'ok')
    versions = {'PyYAML': '6.0', 'httpcore': '1.0'}
    monkeypatch.setattr('harness.p2_checkpoint_identity.metadata.version',
                        lambda name: versions.get(name, 'pinned'))
    def identity():
        return checkpoint_identity(runners={'subject': runner}, files=[], configuration={})
    original = identity()
    versions['PyYAML'] = '6.1'
    assert identity() != original
    versions['PyYAML'] = '6.0'
    assert identity() == original
    versions['httpcore'] = '2.0'
    assert identity() != original
