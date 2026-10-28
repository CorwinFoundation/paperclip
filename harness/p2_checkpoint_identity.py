"""Explicit, credential-free checkpoint identities for P2 (BEAAA-2299)."""
from __future__ import annotations

import hashlib
import json
import os
from importlib import metadata
from pathlib import Path
from typing import Any

from harness.runner import AnthropicRunner, ModelRunner, OpenAIRunner


def runner_identity(runner: ModelRunner) -> dict[str, Any]:
    """Reject unknown adapters instead of mistaking a display name for identity.

    Custom adapters may expose p2_checkpoint_identity() returning a nonempty
    JSON object containing all behavior-affecting configuration, without secrets.
    Exact types are deliberate: subclasses can change behavior or add settings.
    """
    fields = {
        AnthropicRunner: ('_model', '_max_tokens', '_temperature', '_system_prompt',
                          '_cache_prompt', '_batch', '_batch_poll_seconds', '_response_schema'),
        OpenAIRunner: ('_model', '_max_tokens', '_temperature', '_seed', '_system_prompt'),
    }
    if type(runner) in fields:
        config = {field: getattr(runner, field) for field in fields[type(runner)]}
        # SDK clients are constructed at call time and consume ambient routing
        # and account settings. Hash values so endpoints or account identifiers
        # containing credentials never appear in the returned identity.
        sdk, names = (
            ('openai', ('OPENAI_BASE_URL', 'OPENAI_ORG_ID', 'OPENAI_PROJECT_ID',
                        'OPENAI_API_KEY')) if type(runner) is OpenAIRunner else
            ('anthropic', ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY',
                           'ANTHROPIC_AUTH_TOKEN'))
        )
        names += ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                  'SSL_CERT_FILE', 'SSL_CERT_DIR')
        config['environment'] = {
            name: hashlib.sha256(os.environ[name].encode()).hexdigest()
            if name in os.environ else None for name in names
        }
        try:
            config['sdk_version'] = metadata.version(sdk)
        except metadata.PackageNotFoundError:
            config['sdk_version'] = None
        config['runner_source'] = hashlib.sha256(
            Path(__file__).with_name('runner.py').read_bytes()).hexdigest()
    else:
        provider = getattr(runner, 'p2_checkpoint_identity', None)
        if not callable(provider):
            raise ValueError('runner does not support persistent P2 checkpoint identity')
        config = provider()
        if not isinstance(config, dict) or not config:
            raise ValueError('P2 runner checkpoint identity must be a nonempty JSON object')
    # Validate before any worker or target call; do not stringify opaque objects.
    json.dumps(config, sort_keys=True, allow_nan=False)
    return {'adapter': type(runner).__module__ + '.' + type(runner).__qualname__,
            'config': config}


def runtime_identity() -> dict[str, Any]:
    """Fingerprint runtime dependencies without importing provider clients."""
    import platform
    dependencies = {}
    for name in ('PyYAML', 'openai', 'anthropic', 'httpx', 'httpcore', 'pydantic'):
        try:
            dependencies[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            dependencies[name] = None
    return {'python': platform.python_version(), 'dependencies': dependencies}


def checkpoint_identity(*, runners: dict[str, ModelRunner], files: list[Path],
                        configuration: dict[str, Any]) -> str:
    """Hash complete supplied inputs; only the digest is persisted."""
    value = {
        'version': 2,
        # Parsing, numeric/runtime and transport changes can affect aggregates
        # even when the caller's source and model settings are unchanged.
        'runtime': runtime_identity(),
        'runners': {name: runner_identity(runner) for name, runner in runners.items()},
        'files': [hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in files],
        'configuration': configuration,
    }
    encoded = json.dumps(value, sort_keys=True, allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()
