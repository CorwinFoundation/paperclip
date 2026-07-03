"""Runtime profile guards for production Scanner startup.

BEAAA-8882 hardens the API edge against dev-only shortcuts in pilot/prod
profiles without changing score shape or scoring math.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Mapping

from .bundle import BundleStore, FileBundleStorage


RUNTIME_PROFILE_ENV = "ARE_RUNTIME_PROFILE"
BUNDLE_STORAGE_ENV = "ARE_BUNDLE_STORAGE"
BUNDLE_STORAGE_DIR_ENV = "ARE_BUNDLE_STORAGE_DIR"
CONNECTOR_PRIVATE_KEY_FILE_ENV = "ARE_CONNECTOR_PRIVATE_KEY_FILE"

LOCAL_DEV = "local_dev"
CI = "ci"
DEMO = "demo"
PILOT = "pilot"
PROD = "prod"

ALLOWED_RUNTIME_PROFILES = frozenset({LOCAL_DEV, CI, DEMO, PILOT, PROD})
STRICT_RUNTIME_PROFILES = frozenset({PILOT, PROD})


class RuntimeGuardError(ValueError):
    """Raised when the runtime profile is not safe to start."""


def current_runtime_profile(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    profile = source.get(RUNTIME_PROFILE_ENV, LOCAL_DEV).strip().lower()
    if profile not in ALLOWED_RUNTIME_PROFILES:
        allowed = ", ".join(sorted(ALLOWED_RUNTIME_PROFILES))
        raise RuntimeGuardError(f"{RUNTIME_PROFILE_ENV} must be one of: {allowed}")
    return profile


def bundle_store_from_env(env: Mapping[str, str] | None = None) -> BundleStore:
    source = os.environ if env is None else env
    storage_kind = source.get(BUNDLE_STORAGE_ENV, "memory").strip().lower()
    if storage_kind in ("memory", "in_memory", ""):
        return BundleStore()
    if storage_kind in ("file", "filesystem"):
        storage_dir = source.get(BUNDLE_STORAGE_DIR_ENV)
        if not storage_dir:
            raise RuntimeGuardError(f"{BUNDLE_STORAGE_DIR_ENV} required for file bundle storage")
        return BundleStore(storage=FileBundleStorage(storage_dir))
    raise RuntimeGuardError(f"unknown {BUNDLE_STORAGE_ENV}: {storage_kind}")


def assert_strict_private_key_permissions(path: str | Path) -> None:
    key_path = Path(path)
    try:
        st = key_path.stat()
    except OSError as exc:
        raise RuntimeGuardError(f"connector private key file unreadable: {key_path}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise RuntimeGuardError("connector private key path must be a regular file")
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise RuntimeGuardError(
            "connector private key file must not be group- or world-accessible"
        )


def validate_runtime_environment(
    *,
    profile: str | None = None,
    bundles: BundleStore,
    scan_runtime: Any | None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Fail closed on unsafe pilot/prod profile combinations."""
    source = os.environ if env is None else env
    selected = profile or current_runtime_profile(source)
    if selected not in ALLOWED_RUNTIME_PROFILES:
        raise RuntimeGuardError(f"invalid runtime profile: {selected}")
    if selected not in STRICT_RUNTIME_PROFILES:
        key_file = source.get(CONNECTOR_PRIVATE_KEY_FILE_ENV)
        if key_file:
            assert_strict_private_key_permissions(key_file)
        return

    if source.get("ARE_ALLOW_DEV_SIGNING_KEY", "").strip().lower() == "true":
        raise RuntimeGuardError("dev signing key is forbidden in pilot/prod")
    if not source.get("ARE_BUNDLE_SIGNING_KEY"):
        raise RuntimeGuardError("ARE_BUNDLE_SIGNING_KEY required in pilot/prod")
    if bundles.is_in_memory:
        raise RuntimeGuardError("in-memory forensic bundle storage is forbidden in pilot/prod")

    key_file = source.get(CONNECTOR_PRIVATE_KEY_FILE_ENV)
    if key_file:
        assert_strict_private_key_permissions(key_file)

    guard_flags = getattr(scan_runtime, "runtime_guard_flags", {}) or {}
    if guard_flags.get("synthetic_walker"):
        raise RuntimeGuardError("synthetic walkers are forbidden in pilot/prod")
    if guard_flags.get("mock_scanner_path"):
        raise RuntimeGuardError("mock scanner paths are forbidden in pilot/prod")

