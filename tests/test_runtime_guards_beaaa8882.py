from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from api.bundle import BundleStore, FileBundleStorage
from api.runtime_guards import (
    RuntimeGuardError,
    assert_strict_private_key_permissions,
    bundle_store_from_env,
    validate_runtime_environment,
)


class _Runtime:
    def __init__(self, **flags: bool) -> None:
        self.runtime_guard_flags = flags


def _env_without_guard_vars() -> dict[str, str]:
    blocked = {
        "ARE_RUNTIME_PROFILE",
        "ARE_BUNDLE_SIGNING_KEY",
        "ARE_EVAL_PROVENANCE_KEY",
        "ARE_ALLOW_DEV_SIGNING_KEY",
        "ARE_BUNDLE_STORAGE",
        "ARE_BUNDLE_STORAGE_DIR",
        "ARE_CONNECTOR_PRIVATE_KEY_FILE",
        "ARE_PRODUCTION_SCAN_WIRING",
    }
    return {k: v for k, v in os.environ.items() if k not in blocked}


def test_pilot_refuses_dev_signing_key(tmp_path: Path):
    bundles = BundleStore(storage=FileBundleStorage(tmp_path))
    env = {
        "ARE_RUNTIME_PROFILE": "pilot",
        "ARE_BUNDLE_SIGNING_KEY": "prod-key",
        "ARE_ALLOW_DEV_SIGNING_KEY": "true",
    }
    with pytest.raises(RuntimeGuardError, match="dev signing key"):
        validate_runtime_environment(
            profile="pilot",
            bundles=bundles,
            scan_runtime=_Runtime(),
            env=env,
        )


def test_prod_refuses_in_memory_bundle_storage():
    # BEAAA-10945 — a valid-up-to-this-point prod env now also carries the
    # provenance key (same custody class as the bundle signing key); it is set
    # here so the assertion reaches the in-memory-storage guard.
    env = {
        "ARE_RUNTIME_PROFILE": "prod",
        "ARE_BUNDLE_SIGNING_KEY": "prod-key",
        "ARE_EVAL_PROVENANCE_KEY": "prod-provenance-key",
    }
    with pytest.raises(RuntimeGuardError, match="in-memory forensic bundle storage"):
        validate_runtime_environment(
            profile="prod",
            bundles=BundleStore(),
            scan_runtime=_Runtime(),
            env=env,
        )


@pytest.mark.parametrize("flag", ["synthetic_walker", "mock_scanner_path"])
def test_prod_refuses_mock_or_synthetic_scan_paths(tmp_path: Path, flag: str):
    bundles = BundleStore(storage=FileBundleStorage(tmp_path))
    env = {
        "ARE_RUNTIME_PROFILE": "prod",
        "ARE_BUNDLE_SIGNING_KEY": "prod-key",
        "ARE_EVAL_PROVENANCE_KEY": "prod-provenance-key",
    }
    with pytest.raises(RuntimeGuardError, match="forbidden in pilot/prod"):
        validate_runtime_environment(
            profile="prod",
            bundles=bundles,
            scan_runtime=_Runtime(**{flag: True}),
            env=env,
        )


def test_connector_private_key_file_must_not_be_world_readable(tmp_path: Path):
    key = tmp_path / "connector.key"
    key.write_text("private-key", encoding="utf-8")
    key.chmod(0o644)
    with pytest.raises(RuntimeGuardError, match="group- or world-accessible"):
        assert_strict_private_key_permissions(key)


def test_connector_private_key_file_accepts_owner_only_permissions(tmp_path: Path):
    key = tmp_path / "connector.key"
    key.write_text("private-key", encoding="utf-8")
    key.chmod(0o600)
    assert_strict_private_key_permissions(key)


def test_bundle_store_from_env_builds_file_storage(tmp_path: Path):
    store = bundle_store_from_env(
        {
            "ARE_BUNDLE_STORAGE": "file",
            "ARE_BUNDLE_STORAGE_DIR": str(tmp_path),
        }
    )
    assert not store.is_in_memory


def test_create_app_refuses_prod_with_in_memory_storage():
    from api.app import create_app

    env = {
        **_env_without_guard_vars(),
        "ARE_RUNTIME_PROFILE": "prod",
        "ARE_BUNDLE_SIGNING_KEY": "prod-key",
        "ARE_EVAL_PROVENANCE_KEY": "prod-provenance-key",
        "ARE_BUNDLE_STORAGE": "memory",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(RuntimeGuardError, match="in-memory forensic bundle storage"):
            create_app()


def test_create_app_refuses_prod_with_missing_signing_key(tmp_path: Path):
    from api.app import create_app

    env = {
        **_env_without_guard_vars(),
        "ARE_RUNTIME_PROFILE": "prod",
        "ARE_BUNDLE_STORAGE": "file",
        "ARE_BUNDLE_STORAGE_DIR": str(tmp_path),
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValueError, match="ARE_BUNDLE_SIGNING_KEY"):
            create_app()


def test_prod_refuses_missing_eval_provenance_key(tmp_path: Path):
    # BEAAA-10945 (SEC-6 live edge) — the provenance key is mandatory in
    # pilot/prod (same custody class as the bundle signing key). Without it the
    # intake verifier is inert and forged hosted-run provenance is admitted, so
    # the runtime must refuse to start.
    bundles = BundleStore(storage=FileBundleStorage(tmp_path))
    env = {"ARE_RUNTIME_PROFILE": "prod", "ARE_BUNDLE_SIGNING_KEY": "prod-key"}
    with pytest.raises(RuntimeGuardError, match="ARE_EVAL_PROVENANCE_KEY"):
        validate_runtime_environment(
            profile="prod",
            bundles=bundles,
            scan_runtime=_Runtime(),
            env=env,
        )


def test_create_app_refuses_prod_with_dev_signing_key_escape_hatch(tmp_path: Path):
    from api.app import create_app

    env = {
        **_env_without_guard_vars(),
        "ARE_RUNTIME_PROFILE": "prod",
        "ARE_ALLOW_DEV_SIGNING_KEY": "true",
        "ARE_BUNDLE_STORAGE": "file",
        "ARE_BUNDLE_STORAGE_DIR": str(tmp_path),
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(RuntimeGuardError, match="dev signing key"):
            create_app()
